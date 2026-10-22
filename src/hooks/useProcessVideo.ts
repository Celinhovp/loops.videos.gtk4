export async function prepareVideoForUpload(
    uri: string,
    onProgress?: (pct: number) => void
): Promise<string> {
    if (onProgress) {
        onProgress(50);
        setTimeout(() => onProgress(100), 100);
    }
    return uri;
}

export default prepareVideoForUpload;
