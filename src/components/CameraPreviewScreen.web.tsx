import { Ionicons } from '@expo/vector-icons';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import React, { useEffect, useRef, useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

export default function DesktopVideoPreviewScreen() {
    const router = useRouter();
    const { videoPath, duration, fileName } = useLocalSearchParams<{
        videoPath: string;
        duration?: string;
        fileName?: string;
    }>();

    const videoRef = useRef<HTMLVideoElement>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [playbackError, setPlaybackError] = useState(false);
    const [mediaErrorCode, setMediaErrorCode] = useState<number | null>(null);

    // Release the decoder when leaving preview. Keep the source URL intact because
    // the next screen uses the same file/blob to publish the video.
    useEffect(() => () => {
        videoRef.current?.pause();
    }, []);

    const playPreview = () => {
        const video = videoRef.current;
        if (!video) return;

        try {
            const result = video.play();
            if (result && typeof result.then === 'function') {
                result.then(() => {
                    // The playing event is authoritative for the button state.
                    setPlaybackError(false);
                    setMediaErrorCode(null);
                }).catch(() => {
                    setIsPlaying(false);
                    setPlaybackError(true);
                    setMediaErrorCode(video.error?.code ?? null);
                });
            }
        } catch {
                setIsPlaying(false);
                setPlaybackError(true);
                setMediaErrorCode(video.error?.code ?? null);
        }
    };

    const togglePlayback = () => {
        const video = videoRef.current;
        if (!video) return;
        if (video.paused) playPreview();
        else {
            video.pause();
            setIsPlaying(false);
        }
    };

    const continueToCaption = () => {
        videoRef.current?.pause();
        router.push({
            pathname: '/private/camera/caption',
            params: { videoPath, duration: duration || '0', fileName },
        });
    };

    return (
        <View style={styles.container}>
            <Stack.Screen options={{ headerShown: false }} />
            {React.createElement('video', {
                ref: videoRef,
                src: videoPath,
                controls: true,
                // Start only from an explicit user action. WebKit can emit
                // loadeddata again after seeking; calling play there restarted
                // videos even after the user had paused them.
                autoPlay: false,
                muted: true,
                defaultMuted: true,
                playsInline: true,
                loop: true,
                preload: 'auto',
                style: styles.video,
                onPlaying: () => {
                    setIsPlaying(true);
                    setPlaybackError(false);
                    setMediaErrorCode(null);
                },
                onPause: () => setIsPlaying(false),
                onError: (event: React.SyntheticEvent<HTMLVideoElement>) => {
                    const code = event.currentTarget.error?.code ?? null;
                    console.warn('Video preview failed', {
                        code,
                        message: event.currentTarget.error?.message,
                        source: videoPath,
                    });
                    setIsPlaying(false);
                    setPlaybackError(true);
                    setMediaErrorCode(code);
                },
            })}
            {!isPlaying && !playbackError && (
                <TouchableOpacity
                    accessibilityLabel="Reproduzir vídeo"
                    onPress={playPreview}
                    style={styles.playButton}
                >
                    <Ionicons name="play" size={38} color="#111" />
                </TouchableOpacity>
            )}
            {playbackError && (
                <TouchableOpacity onPress={togglePlayback} style={styles.errorMessage}>
                    <Ionicons name="refresh" size={22} color="#fff" />
                    <Text style={styles.errorText}>Não foi possível reproduzir este vídeo</Text>
                    <Text style={styles.errorHint}>
                        {mediaErrorCode === 4
                            ? 'Formato ou codec não compatível. Tente um MP4 com vídeo H.264.'
                            : mediaErrorCode === 3
                              ? 'Falha ao decodificar o vídeo. Tente um MP4 com vídeo H.264.'
                              : 'Toque para tentar novamente'}
                    </Text>
                </TouchableOpacity>
            )}
            <View style={styles.topBar}>
                <TouchableOpacity onPress={() => router.back()} style={styles.iconButton}>
                    <Ionicons name="chevron-back" size={28} color="#fff" />
                </TouchableOpacity>
                <Text style={styles.header}>Preview video</Text>
                <View style={styles.iconButton} />
            </View>
            <View style={styles.bottomBar}>
                <TouchableOpacity onPress={continueToCaption} style={styles.nextButton}>
                    <Text style={styles.nextLabel}>Continue</Text>
                    <Ionicons name="arrow-forward" size={20} color="#111" />
                </TouchableOpacity>
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: '#000', justifyContent: 'center' },
    video: {
        width: '100%',
        height: '100%',
        position: 'absolute',
        top: 0,
        right: 0,
        bottom: 0,
        left: 0,
        objectFit: 'contain',
        backgroundColor: '#000',
    },
    topBar: {
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        height: 68,
        paddingHorizontal: 18,
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'center',
        backgroundColor: 'rgba(0,0,0,0.35)',
    },
    iconButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
    playButton: {
        position: 'absolute',
        alignSelf: 'center',
        width: 76,
        height: 76,
        borderRadius: 38,
        backgroundColor: '#d7c500',
        alignItems: 'center',
        justifyContent: 'center',
    },
    errorMessage: {
        position: 'absolute',
        alignSelf: 'center',
        alignItems: 'center',
        gap: 8,
        padding: 20,
        borderRadius: 12,
        backgroundColor: 'rgba(0,0,0,0.75)',
    },
    errorText: { color: '#fff', fontSize: 16, fontWeight: '700' },
    errorHint: { color: '#ccc', fontSize: 13 },
    header: { color: '#fff', fontSize: 17, fontWeight: '700' },
    bottomBar: {
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: 0,
        padding: 24,
        alignItems: 'flex-end',
        backgroundColor: 'rgba(0,0,0,0.35)',
    },
    nextButton: {
        minWidth: 156,
        height: 50,
        paddingHorizontal: 24,
        borderRadius: 25,
        backgroundColor: '#d7c500',
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 10,
    },
    nextLabel: { color: '#111', fontSize: 15, fontWeight: '700' },
});
