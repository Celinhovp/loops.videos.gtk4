import React from 'react';
import { Modal, Pressable, StyleSheet, Text, View, ScrollView } from 'react-native';
import tw from 'twrnc';
import type { DmConversation } from '@/types/dm';
import { conversationDisplayName, conversationParticipants } from './dmGroupHelpers';

interface GroupInfoSheetProps {
    visible: boolean;
    conversation: DmConversation;
    onClose: () => void;
    onLeft?: () => void;
    onOpenProfile?: (profileId: string | number) => void;
}

export function GroupInfoSheet({
    visible,
    conversation,
    onClose,
    onLeft,
    onOpenProfile,
}: GroupInfoSheetProps) {
    if (!visible) return null;

    const participants = conversationParticipants(conversation);
    const title = conversationDisplayName(conversation);

    return (
        <Modal
            transparent
            visible={visible}
            animationType="slide"
            onRequestClose={onClose}>
            <Pressable style={styles.backdrop} onPress={onClose}>
                <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
                    <View style={tw`w-10 h-1 bg-zinc-600 rounded-full self-center mb-4`} />
                    <Text style={tw`text-white font-bold text-lg mb-2 text-center`}>{title}</Text>
                    <Text style={tw`text-zinc-400 text-sm mb-4 text-center`}>
                        {participants.length} members
                    </Text>
                    <ScrollView style={tw`max-h-60 mb-4`}>
                        {participants.map((p) => (
                            <Pressable
                                key={p.id}
                                style={tw`flex-row items-center py-2 px-1 justify-between`}
                                onPress={() => onOpenProfile && onOpenProfile(p.id)}>
                                <Text style={tw`text-white font-medium`}>{p.name || p.username}</Text>
                                <Text style={tw`text-zinc-400 text-xs`}>@{p.username}</Text>
                            </Pressable>
                        ))}
                    </ScrollView>
                    {onLeft && (
                        <Pressable
                            onPress={onLeft}
                            style={tw`bg-red-500/20 py-2.5 rounded-xl items-center`}>
                            <Text style={tw`text-red-400 font-semibold`}>Leave group</Text>
                        </Pressable>
                    )}
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
        padding: 20,
    },
});

export default GroupInfoSheet;
