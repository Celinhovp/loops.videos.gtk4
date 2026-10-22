import React from 'react';
import { Modal, Pressable, StyleSheet, Image, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { DmMediaEntity } from '@/types/dm';

interface ImageViewerProps {
    visible: boolean;
    media: DmMediaEntity | null;
    onClose: () => void;
}

export function ImageViewer({ visible, media, onClose }: ImageViewerProps) {
    if (!visible || !media) return null;

    const sourceUri = media.url || media.preview_url;

    return (
        <Modal
            transparent
            visible={visible}
            animationType="fade"
            onRequestClose={onClose}>
            <View style={styles.container}>
                <Pressable style={styles.closeButton} onPress={onClose}>
                    <Ionicons name="close" size={28} color="#FFFFFF" />
                </Pressable>
                {sourceUri && (
                    <Image
                        source={{ uri: sourceUri }}
                        style={styles.image}
                        resizeMode="contain"
                    />
                )}
            </View>
        </Modal>
    );
}

const styles = StyleSheet.create({
    container: {
        flex: 1,
        backgroundColor: '#000000',
        justifyContent: 'center',
        alignItems: 'center',
    },
    closeButton: {
        position: 'absolute',
        top: 50,
        right: 20,
        zIndex: 10,
        padding: 8,
    },
    image: {
        width: '100%',
        height: '100%',
    },
});

export default ImageViewer;
