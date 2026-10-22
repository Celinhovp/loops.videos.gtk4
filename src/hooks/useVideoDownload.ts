import { useState, useCallback } from 'react';

export function useVideoDownload() {
    const [status, setStatus] = useState<'idle' | 'downloading' | 'completed' | 'error'>('idle');
    const [progress, setProgress] = useState(0);
    const [error, setError] = useState<string | null>(null);

    const download = useCallback(async (url?: string) => {
        if (!url) return;
        setStatus('downloading');
        setProgress(0);
        // On web or desktop, trigger a standard download if available
        if (typeof window !== 'undefined' && window.document) {
            try {
                const a = document.createElement('a');
                a.href = url;
                a.download = 'video.mp4';
                a.target = '_blank';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                setStatus('completed');
                setProgress(100);
            } catch (err: any) {
                setError(err?.message || 'Download failed');
                setStatus('error');
            }
        }
    }, []);

    const cancel = useCallback(() => {
        setStatus('idle');
        setProgress(0);
    }, []);

    return {
        status,
        progress,
        error,
        download,
        cancel,
    };
}

export default useVideoDownload;
