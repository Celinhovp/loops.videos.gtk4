export function isGroupConversation(conversation) {
    return !!conversation?.is_group || (Array.isArray(conversation?.participants) && conversation.participants.length > 2);
}

export function conversationParticipants(conversation) {
    return Array.isArray(conversation?.participants) ? conversation.participants : [];
}

export function conversationDisplayName(conversation) {
    if (conversation?.title) return conversation.title;
    if (conversation?.name) return conversation.name;
    const parts = conversationParticipants(conversation);
    return parts.map((p) => p.name || p.username).filter(Boolean).join(', ') || 'Conversation';
}

export function conversationMemberCount(conversation) {
    if (typeof conversation?.member_count === 'number') return conversation.member_count;
    return conversationParticipants(conversation).length;
}
