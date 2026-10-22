import React from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';
import tw from 'twrnc';

interface DownloadToastProps {
    status?: 'idle' | 'downloading' | 'completed' | 'error';
    progress?: number;
    error?: string | null;
    onCancel?: () => void;
}

export function DownloadToast({ status, progress = 0, error, onCancel }: DownloadToastProps) {
    if (!status || status === 'idle') return null;

    return (
        <View style={styles.container}>
            <View style={tw`bg-zinc-800 rounded-lg px-4 py-3 flex-row items-center justify-between border border-zinc-700 shadow-lg`}>
                <View style={tw`flex-1 mr-3`}>
                    <Text style={tw`text-white font-medium text-sm`}>
                        {status === 'downloading' && `Downloading... ${Math.round(progress)}%`}
                        {status === 'completed' && 'Download completed!'}
                        {status === 'error' && (error || 'Download failed')}
                    </Text>
                </View>
                {onCancel && status === 'downloading' && (
                    <Pressable onPress={onCancel} style={tw`px-2 py-1 bg-zinc-700 rounded`}>
                        <Text style={tw`text-white text-xs`}>Cancel</Text>
                    </Pressable>
                )}
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    container: {
        position: 'absolute',
        bottom: 80,
        left: 20,
        right: 20,
        zIndex: 9999,
    },
});

export default DownloadToast;
