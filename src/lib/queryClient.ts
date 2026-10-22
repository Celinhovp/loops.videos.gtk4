import { createSyncStoragePersister } from '@tanstack/query-sync-storage-persister';
import { QueryClient, focusManager } from '@tanstack/react-query';
import { persistQueryClient } from '@tanstack/react-query-persist-client';
import { AppState, Platform } from 'react-native';
import { createMMKV } from 'react-native-mmkv';
import type { AppConfig } from '../services/config';
import { useConfigStore } from '../stores/configStore';

const ONE_DAY = 1000 * 60 * 60 * 24;

import { Storage } from '../utils/cache';

const mmkvStorage = {
    getItem: (key: string): string | null => {
        try {
            const v = Storage.getString(key);
            return v === undefined ? null : v;
        } catch {
            return null;
        }
    },
    setItem: (key: string, value: string): void => {
        try {
            Storage.set(key, value);
        } catch {}
    },
    removeItem: (key: string): void => {
        try {
            Storage.remove(key);
        } catch {}
    },
};

export const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            gcTime: ONE_DAY * 7,
        },
    },
});

const persister = createSyncStoragePersister({
    storage: mmkvStorage,
    key: 'LOOPS_QUERY_CACHE',
});

persistQueryClient({
    queryClient,
    persister,
    maxAge: ONE_DAY * 7,
    dehydrateOptions: {
        // Keep public server config and account-scoped feed pages on disk so
        // reopening the desktop app can paint cached videos immediately.
        shouldDehydrateQuery: (q) =>
            q.queryKey[0] === 'server-config' || q.queryKey[0] === 'videos',
    },
});

AppState.addEventListener('change', (status) => {
    if (Platform.OS !== 'web') {
        focusManager.setFocused(status === 'active');
    }
});

const cached = queryClient.getQueryData<AppConfig>(['server-config']);
if (cached) useConfigStore.getState().setConfig(cached);

queryClient.getQueryCache().subscribe((event) => {
    if (event.query.queryKey[0] !== 'server-config') return;
    const data = event.query.state.data as AppConfig | undefined;
    useConfigStore.getState().setConfig(data ?? null);
});
