import { PressableHaptics } from '@/components/ui/PressableHaptics';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import { Stack, useRouter } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Alert, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

export default function DesktopCreateScreen() {
    const router = useRouter();
    const [isSelecting, setIsSelecting] = useState(false);

    const selectVideo = async () => {
        if (isSelecting) return;
        setIsSelecting(true);
        try {
            const result = await ImagePicker.launchImageLibraryAsync({
                mediaTypes: ['videos'],
                allowsEditing: false,
                exif: false,
                quality: 1,
                selectionLimit: 1,
                videoMaxDuration: 180,
            });

            const asset = result.assets?.[0];
            if (asset) {
                router.push({
                    pathname: '/private/camera/preview',
                    params: {
                        videoPath: asset.uri,
                        duration: Math.round((asset.duration || 0) / 1000),
                        ...(asset.fileName ? { fileName: asset.fileName } : {}),
                    },
                });
            }
        } catch (error) {
            console.error('Video selection failed:', error);
            Alert.alert('Could not open video', 'Please try selecting the video again.');
        } finally {
            setIsSelecting(false);
        }
    };

    return (
        <View style={styles.container}>
            <Stack.Screen options={{ headerShown: false }} />
            <View style={styles.topBar}>
                <PressableHaptics
                    onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))}
                    style={styles.closeButton}>
                    <Ionicons name="close" size={28} color="#fff" />
                </PressableHaptics>
                <Text style={styles.header}>Create a Loop</Text>
                <View style={styles.closeButton} />
            </View>

            <View style={styles.content}>
                <View style={styles.iconCircle}>
                    <Ionicons name="videocam-outline" size={42} color="#d7c500" />
                </View>
                <Text style={styles.title}>Choose a video</Text>
                <Text style={styles.description}>
                    Select a video from your computer to preview and publish.
                </Text>
                <TouchableOpacity
                    style={[styles.selectButton, isSelecting && styles.disabledButton]}
                    onPress={selectVideo}
                    disabled={isSelecting}>
                    {isSelecting ? (
                        <ActivityIndicator color="#111" />
                    ) : (
                        <>
                            <Ionicons name="cloud-upload-outline" size={21} color="#111" />
                            <Text style={styles.selectButtonText}>Select video</Text>
                        </>
                    )}
                </TouchableOpacity>
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: '#09090b' },
    topBar: {
        height: 64,
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingHorizontal: 18,
        borderBottomWidth: StyleSheet.hairlineWidth,
        borderBottomColor: 'rgba(255,255,255,0.12)',
    },
    closeButton: { width: 42, height: 42, alignItems: 'center', justifyContent: 'center' },
    header: { color: '#fff', fontSize: 17, fontWeight: '700' },
    content: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
    iconCircle: {
        width: 80,
        height: 80,
        borderRadius: 40,
        backgroundColor: 'rgba(215,197,0,0.12)',
        alignItems: 'center',
        justifyContent: 'center',
        marginBottom: 22,
    },
    title: { color: '#fff', fontSize: 22, fontWeight: '700', marginBottom: 10 },
    description: {
        color: '#a1a1aa',
        fontSize: 15,
        textAlign: 'center',
        maxWidth: 360,
        lineHeight: 22,
        marginBottom: 26,
    },
    selectButton: {
        minWidth: 190,
        height: 50,
        paddingHorizontal: 22,
        borderRadius: 25,
        backgroundColor: '#d7c500',
        flexDirection: 'row',
        gap: 9,
        alignItems: 'center',
        justifyContent: 'center',
    },
    disabledButton: { opacity: 0.7 },
    selectButtonText: { color: '#111', fontSize: 15, fontWeight: '700' },
});
