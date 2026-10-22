import React from 'react';
import { Modal, Pressable, StyleSheet, View } from 'react-native';
import tw from 'twrnc';

interface BottomSheetModalProps {
    visible: boolean;
    onClose: () => void;
    children: React.ReactNode;
}

export function BottomSheetModal({ visible, onClose, children }: BottomSheetModalProps) {
    if (!visible) return null;

    return (
        <Modal
            transparent
            visible={visible}
            animationType="slide"
            onRequestClose={onClose}>
            <Pressable style={styles.backdrop} onPress={onClose}>
                <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
                    <View style={tw`w-10 h-1.5 bg-gray-300 dark:bg-zinc-700 rounded-full self-center mb-4`} />
                    {children}
                </Pressable>
            </Pressable>
        </Modal>
    );
}

const styles = StyleSheet.create({
    backdrop: {
        flex: 1,
        backgroundColor: 'rgba(0,0,0,0.5)',
        justifyContent: 'flex-end',
    },
    sheet: {
        backgroundColor: '#18181b',
        borderTopLeftRadius: 20,
        borderTopRightRadius: 20,
        padding: 16,
        paddingBottom: 32,
    },
});

export default BottomSheetModal;
