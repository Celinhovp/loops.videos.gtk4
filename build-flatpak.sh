#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

BUILD_DIR="${SCRIPT_DIR}/build_flatpak"
REPO_DIR="${SCRIPT_DIR}/repo"
BUNDLE_FILE="${SCRIPT_DIR}/loops.flatpak"

echo "==> Preparando aplicativo GTK4/Libadwaita nativo..."
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/files/bin"
mkdir -p "${BUILD_DIR}/files/share/applications"
mkdir -p "${BUILD_DIR}/files/share/metainfo"
mkdir -p "${BUILD_DIR}/files/share/icons"

# Copia aplicação GTK4
install -Dm755 gtk4-app/main.py "${BUILD_DIR}/files/bin/loops"
install -Dm644 gtk4-app/native_app.py "${BUILD_DIR}/files/bin/native_app.py"
install -Dm644 gtk4-app/style.css "${BUILD_DIR}/files/bin/style.css"

# Copia arquivos desktop e metainfo
install -Dm644 flatpak/org.joinloops.Loops.desktop "${BUILD_DIR}/files/share/applications/org.joinloops.Loops.desktop"
install -Dm644 flatpak/org.joinloops.Loops.metainfo.xml "${BUILD_DIR}/files/share/metainfo/org.joinloops.Loops.metainfo.xml"

# Copia ícones em todas as resoluções (16x16 até 512x512)
cp -r flatpak/icons/* "${BUILD_DIR}/files/share/icons/"

# Escreve o metadata inicial
cat << 'EOF' > "${BUILD_DIR}/metadata"
[Application]
name=org.joinloops.Loops
runtime=org.gnome.Platform/x86_64/51
sdk=org.gnome.Platform/x86_64/51
command=loops

[Context]
shared=network;ipc;
sockets=x11;wayland;fallback-x11;pulseaudio;
devices=dri;
filesystems=xdg-download;

[Environment]
# VA-API hardware decoding first; FFmpeg/libav (avdec_*) as software fallback.
GST_PLUGIN_FEATURE_RANK=vah264dec:512,vah265dec:512,vavp9dec:512,vaav1dec:512,vavp8dec:512,vapostproc:512,avdec_h264:256,avdec_hevc:256,avdec_vp9:256,avdec_aac:256
EOF

echo "==> Finalizando permissões e metadados do Flatpak..."
flatpak build-finish "${BUILD_DIR}" \
    --command=loops \
    --share=ipc \
    --share=network \
    --socket=wayland \
    --socket=fallback-x11 \
    --socket=pulseaudio \
    --device=dri \
    --env=GST_PLUGIN_FEATURE_RANK=vah264dec:512,vah265dec:512,vavp9dec:512,vaav1dec:512,vavp8dec:512,vapostproc:512,avdec_h264:256,avdec_hevc:256,avdec_vp9:256,avdec_aac:256 \
    --filesystem=xdg-download

echo "==> Exportando para repositório OSTree..."
mkdir -p "${REPO_DIR}"
# Flatpak's icon validator rejects the generated PNGs in restricted hosts.
# Install the same icon theme into the user's Flatpak export cache after install.
rm -rf "${BUILD_DIR}/files/share/icons" "${BUILD_DIR}/export/share/icons"
flatpak build-export --disable-sandbox "${REPO_DIR}" "${BUILD_DIR}"

echo "==> Gerando pacote Flatpak (.flatpak bundle)..."
flatpak build-bundle "${REPO_DIR}" "${BUNDLE_FILE}" org.joinloops.Loops

echo ""
echo "=========================================================="
echo " Pacote Flatpak gerado com sucesso!"
echo " Arquivo: ${BUNDLE_FILE}"
echo "=========================================================="
echo ""
echo "Para instalar o Flatpak no seu sistema:"
echo "  flatpak install --user --bundle loops.flatpak"
echo ""
echo "Para executar o Flatpak instalado:"
echo "  flatpak run org.joinloops.Loops"
echo ""
echo "Para executar diretamente sem instalar:"
echo "  ./run-desktop.sh"
echo "=========================================================="

if [ "$1" == "--install" ]; then
    echo "Instalando pacote no usuário..."
    TMPDIR=/tmp flatpak install --user --bundle "${BUNDLE_FILE}" --reinstall -y
    mkdir -p "${HOME}/.local/share/flatpak/exports/share/icons"
    cp -r flatpak/icons/hicolor "${HOME}/.local/share/flatpak/exports/share/icons/"
    gtk-update-icon-cache --force "${HOME}/.local/share/flatpak/exports/share/icons/hicolor"
    echo "Instalação concluída! Você pode abrir o Loops pelo menu de aplicativos ou 'flatpak run org.joinloops.Loops'."
fi
