"""Small, offline-at-inference comment translation using Argos model files.

The model index and language-pair archives are fetched only when the user asks
for a translation. Inference is local through CTranslate2 and SentencePiece.
"""

import json
import os
import re
import threading
import urllib.request
import zipfile
from pathlib import Path

ARGOS_INDEX_URL = (
    "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
)
ARGOS_USER_AGENT = "LoopsGTK4/1.0 (offline comment translation)"
_model_lock = threading.Lock()
_loaded_models = {}


def _normalized_language(code):
    return {"zh-cn": "zh", "zh-tw": "zt"}.get(code.lower(), code.lower())


def _detect_language(text):
    try:
        from langdetect import DetectorFactory, detect_langs
    except ImportError as error:
        raise RuntimeError("O detector local de idioma não está incluído nesta versão.") from error
    DetectorFactory.seed = 0
    try:
        guesses = detect_langs(text)
    except Exception as error:
        raise RuntimeError("Não foi possível reconhecer o idioma deste comentário.") from error
    if not guesses or guesses[0].prob < 0.60:
        raise RuntimeError("Não foi possível reconhecer o idioma deste comentário.")
    return _normalized_language(guesses[0].lang)


def _load_index(data_dir):
    index_path = data_dir / "index.json"
    if not index_path.exists():
        request = urllib.request.Request(
            ARGOS_INDEX_URL, headers={"User-Agent": ARGOS_USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                index_data = response.read()
            json.loads(index_data)
            tmp_path = index_path.with_suffix(".tmp")
            tmp_path.write_bytes(index_data)
            tmp_path.replace(index_path)
        except Exception as error:
            raise RuntimeError(f"Não foi possível obter a lista de modelos locais: {error}") from error
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"A lista local de modelos está inválida: {error}") from error


def _available_pairs(index):
    pairs = {}
    for item in index:
        if item.get("type", "translate") != "translate":
            continue
        source = item.get("from_code")
        target = item.get("to_code")
        if source and target:
            key = (_normalized_language(source), _normalized_language(target))
            # The Argos index is ordered roughly by current package versions;
            # preserve the first entry for each pair.
            pairs.setdefault(key, item)
    return pairs


def _ensure_model(item, packages_dir):
    from_code = _normalized_language(item["from_code"])
    to_code = _normalized_language(item["to_code"])
    version = str(item.get("package_version", "1"))
    safe_version = re.sub(r"[^A-Za-z0-9_.-]", "_", version)
    destination = packages_dir / f"{from_code}_{to_code}_{safe_version}"
    metadata_file = destination / "metadata.json"
    if metadata_file.exists():
        return destination

    links = item.get("links") or []
    link = next((value for value in links if value.startswith("https://")), None)
    if not link:
        raise RuntimeError(f"O modelo {from_code} → {to_code} não tem um link HTTPS válido.")
    request = urllib.request.Request(link, headers={"User-Agent": ARGOS_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            archive_data = response.read()
    except Exception as error:
        raise RuntimeError(f"Não foi possível baixar o modelo {from_code} → {to_code}: {error}") from error

    temp_dir = destination.with_name(destination.name + ".installing")
    if temp_dir.exists():
        import shutil
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, mode=0o700)
    try:
        import io
        with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
            root = temp_dir.resolve()
            for member in archive.infolist():
                target = (temp_dir / member.filename).resolve()
                if target != root and root not in target.parents:
                    raise RuntimeError("O arquivo do modelo contém um caminho inválido.")
            archive.extractall(temp_dir)
        # Current Argos packages place metadata at the archive root. If a
        # package adds a single enclosing directory, flatten that level.
        if not (temp_dir / "metadata.json").exists():
            children = [p for p in temp_dir.iterdir() if p.is_dir()]
            if len(children) == 1 and (children[0] / "metadata.json").exists():
                nested = children[0]
                for child in list(nested.iterdir()):
                    child.replace(temp_dir / child.name)
                nested.rmdir()
        if not (temp_dir / "metadata.json").exists() or not (temp_dir / "model").is_dir():
            raise RuntimeError("O modelo baixado não tem o formato Argos esperado.")
        if destination.exists():
            import shutil
            shutil.rmtree(destination)
        temp_dir.replace(destination)
    except Exception:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return destination


def _get_model(package_dir):
    package_dir = Path(package_dir)
    key = str(package_dir)
    if key in _loaded_models:
        return _loaded_models[key]
    try:
        import ctranslate2
        import sentencepiece
    except ImportError as error:
        raise RuntimeError(
            "As bibliotecas locais CTranslate2 e SentencePiece não estão incluídas nesta versão."
        ) from error

    metadata = json.loads((package_dir / "metadata.json").read_text(encoding="utf-8"))
    tokenizer_path = package_dir / "sentencepiece.model"
    if not tokenizer_path.exists():
        raise RuntimeError("Este modelo não inclui o tokenizador SentencePiece necessário.")
    processor = sentencepiece.SentencePieceProcessor(model_file=str(tokenizer_path))
    try:
        translator = ctranslate2.Translator(
            str(package_dir / "model"),
            device="cpu",
            inter_threads=1,
            intra_threads=0,
            compute_type="int8",
        )
    except Exception:
        # Some model formats do not provide int8 weights; CTranslate2 can still
        # execute them in its automatic CPU compute mode.
        translator = ctranslate2.Translator(
            str(package_dir / "model"), device="cpu", inter_threads=1, intra_threads=0
        )
    _loaded_models[key] = (translator, processor, metadata)
    return _loaded_models[key]


def _translate_with_model(text, package_dir):
    translator, processor, metadata = _get_model(package_dir)
    target_prefix = metadata.get("target_prefix") or ""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    token_chunks = []
    for sentence in filter(None, sentences):
        pieces = processor.encode(sentence, out_type=str)
        while len(pieces) > 120:
            token_chunks.append(pieces[:120])
            pieces = pieces[120:]
        if pieces:
            token_chunks.append(pieces)

    translated_chunks = []
    for pieces in token_chunks:
        if target_prefix:
            result = translator.translate_batch(
                [pieces],
                target_prefix=[[target_prefix]],
                replace_unknowns=True,
                beam_size=2,
                num_hypotheses=1,
                length_penalty=0.2,
            )[0].hypotheses[0]
            translated = processor.decode_pieces(result)
            if translated.startswith(target_prefix):
                translated = translated[len(target_prefix):]
        else:
            result = translator.translate_batch(
                [pieces], replace_unknowns=True, beam_size=2, num_hypotheses=1,
                length_penalty=0.2,
            )[0].hypotheses[0]
            translated = processor.decode_pieces(result)
        translated_chunks.append(translated.lstrip().replace("▁", " ").replace("_", " "))
    return " ".join(translated_chunks).strip()


def translate_comment(text, target_code, data_dir):
    """Return (translated text or None when already in target, source code)."""
    source_code = _detect_language(text)
    target_code = _normalized_language(target_code)
    if source_code == target_code:
        return None, source_code

    data_dir = Path(data_dir)
    packages_dir = data_dir / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _model_lock:
        index = _load_index(data_dir)
        pairs = _available_pairs(index)
        route = [(source_code, target_code)]
        if route[0] not in pairs:
            route = [(source_code, "en"), ("en", target_code)]
        missing = [pair for pair in route if pair not in pairs]
        if missing:
            source, target = missing[0]
            raise RuntimeError(f"Não há modelo aberto disponível para {source} → {target}.")
        intermediate = text
        for pair in route:
            package_dir = _ensure_model(pairs[pair], packages_dir)
            intermediate = _translate_with_model(intermediate, package_dir)
    return intermediate, source_code
