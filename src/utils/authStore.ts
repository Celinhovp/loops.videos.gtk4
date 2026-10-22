import { LoopsUser, OAuthService } from '@/services/oauth';
import { queryClient } from '@/lib/queryClient';
import { useNotificationStore } from '@/utils//notificationStore';
import { setAuthFailureHandler } from '@/utils/authEvents';
import { getPreferences, resetAuthFailureFlag, updatePreferences } from '@/utils/requests';
import * as SecureStore from 'expo-secure-store';
import { Alert, Platform } from 'react-native';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { Storage } from './cache';

const isWeb = Platform.OS === 'web' || typeof window !== 'undefined';
const initialDesktopVideoMuted = (() => {
    try {
        return Storage.getString('desktopVideoMuted') !== 'false';
    } catch {
        return true;
    }
})();

const initialWebAuth = (() => {
    if (typeof window === 'undefined') return { isLoggedIn: false, user: null, server: null };
    try {
        const token = window.localStorage.getItem('user.token') || window.localStorage.getItem('app.token');
        const profile = window.localStorage.getItem('user.profile');
        return {
            isLoggedIn: Boolean(token && profile),
            user: profile ? JSON.parse(profile) : null,
            server: window.localStorage.getItem('user.server') || window.localStorage.getItem('app.instance'),
        };
    } catch {
        return { isLoggedIn: false, user: null, server: null };
    }
})();

const universalStorage = {
    getItem: (key: string): string | null | Promise<string | null> => {
        if (isWeb) {
            try {
                return typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null;
            } catch {
                return null;
            }
        }
        return SecureStore.getItemAsync(key).catch(() => null);
    },
    setItem: async (key: string, value: string): Promise<void> => {
        if (isWeb) {
            try {
                if (typeof localStorage !== 'undefined') {
                    localStorage.setItem(key, value);
                }
            } catch {}
            return;
        }
        try {
            await SecureStore.setItemAsync(key, value);
        } catch {}
    },
    removeItem: async (key: string): Promise<void> => {
        if (isWeb) {
            try {
                if (typeof localStorage !== 'undefined') {
                    localStorage.removeItem(key);
                }
            } catch {}
            return;
        }
        try {
            await SecureStore.deleteItemAsync(key);
        } catch {}
    },
};

type UserState = {
    isLoggedIn: boolean;
    shouldCreateAccount: boolean;
    hasCompletedOnboarding: boolean;
    _hasHydrated: boolean;
    user: LoopsUser | null;
    server: string | null;
    hideForYouFeed: boolean;
    defaultFeed: 'following' | 'local' | 'forYou';
    autoplayVideos: boolean;
    loopVideos: boolean;
    muteOnOpen: boolean;
    desktopVideoMuted: boolean;
    setDesktopVideoMuted: (value: boolean) => void;
    autoExpandCw: boolean;
    loginWithApple: (server: string, credential: any) => Promise<boolean>;
    appearance: 'light' | 'dark' | 'system';
    loginWithOAuth: (server: string, scopes?: string) => Promise<boolean>;
    registerWithWebBrowser: (server: string) => Promise<boolean>;
    refreshAccessToken: () => Promise<boolean>;
    logOut: () => void;
    completeOnboarding: () => void;
    resetOnboarding: () => void;
    setHasHydrated: (value: boolean) => void;
    setUser: (user: LoopsUser, server: string) => void;
    clearUser: () => void;
    syncAuthState: () => void;
    syncPreferencesFromServer: () => Promise<void>;
    setHideForYouFeed: (value: boolean) => Promise<void>;
    setDefaultFeed: (feed: 'following' | 'local' | 'forYou') => Promise<void>;
    updatePreference: (key: string, value: any) => Promise<void>;
};

export const useAuthStore = create(
    persist<UserState>(
        (set, get) => ({
            // Read the desktop WebKit credentials before the router mounts. The
            // persist middleware finishes rehydrating asynchronously, which can
            // otherwise expose the sign-in guard for one render on every launch.
            isLoggedIn: initialWebAuth.isLoggedIn,
            shouldCreateAccount: false,
            hasCompletedOnboarding: false,
            _hasHydrated: false,
            user: initialWebAuth.user,
            server: initialWebAuth.server,
            hideForYouFeed: false,
            defaultFeed: 'local',
            autoplayVideos: true,
            loopVideos: true,
            muteOnOpen: false,
            desktopVideoMuted: initialDesktopVideoMuted,
            autoExpandCw: false,
            appearance: 'light',

            loginWithOAuth: async (server: string, scopes?: string) => {
                try {
                    const success = await OAuthService.login(server, scopes);

                    if (success) {
                        try {
                            const prefs = await getPreferences();
                            if (prefs?.settings) {
                                set((state) => ({
                                    ...state,
                                    hideForYouFeed: prefs.settings.hide_for_you_feed,
                                    defaultFeed: prefs.settings.default_feed,
                                    autoplayVideos: prefs.settings.autoplay_videos,
                                    loopVideos: prefs.settings.loop_videos,
                                    muteOnOpen: prefs.settings.mute_on_open,
                                    autoExpandCw: prefs.settings.auto_expand_cw,
                                    appearance: prefs.settings.appearance,
                                }));
                            }
                        } catch (e) {
                            console.error('prefs sync failed:', e);
                        }
                        get().syncAuthState();
                        await useNotificationStore.getState().refetchBadgeCount();
                        resetAuthFailureFlag();
                    }

                    return success;
                } catch (error) {
                    console.error('OAuth login failed:', error);
                    return false;
                }
            },

            loginWithApple: async (server: string, credential: any) => {
                try {
                    const res = await fetch(`https://${server}/api/v1/auth/apple`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                        body: JSON.stringify({
                            identity_token: credential.identityToken,
                            user_id: credential.user,
                            email: credential.email,
                            full_name: credential.fullName,
                            authorization_code: credential.authorizationCode,
                        }),
                    });

                    if (!res.ok) {
                        const error = await res.json();
                        Alert.alert('Error', error.error || 'Authentication failed');
                        return false;
                    }

                    const data = await res.json();

                    await OAuthService.storeAppleAuthCredentials(data.token, data.user, server);
                    get().syncAuthState();
                    await useNotificationStore.getState().refetchBadgeCount();
                    await get().syncPreferencesFromServer();
                    resetAuthFailureFlag();

                    return true;
                } catch (error) {
                    console.error('Apple login failed:', error);
                    return false;
                }
            },

            registerWithWebBrowser: async (server: string) => {
                try {
                    const success = await OAuthService.registerWithWebBrowser(server);

                    if (success) {
                        get().syncAuthState();
                        await get().syncPreferencesFromServer();
                        await useNotificationStore.getState().refetchBadgeCount();
                    }

                    return success;
                } catch (error) {
                    console.error('Registration failed:', error);
                    return false;
                }
            },

            refreshAccessToken: async () => {
                try {
                    const success = await OAuthService.refreshToken();

                    if (success) {
                        get().syncAuthState();
                    } else {
                        get().logOut();
                    }

                    return success;
                } catch (error) {
                    console.error('Token refresh failed:', error);
                    get().logOut();
                    return false;
                }
            },

            syncAuthState: () => {
                const user = OAuthService.getCurrentUser();
                const server = OAuthService.getCurrentServer();
                const isAuthenticated = OAuthService.isAuthenticated();

                set((state) => ({
                    ...state,
                    isLoggedIn: isAuthenticated,
                    user: user,
                    server: server,
                }));
            },

            /**
             * Syncs preferences from the server
             */
            syncPreferencesFromServer: async () => {
                try {
                    const prefs = await getPreferences();

                    if (prefs && prefs.settings) {
                        set((state) => ({
                            ...state,
                            hideForYouFeed: prefs.settings.hide_for_you_feed,
                            defaultFeed: prefs.settings.default_feed,
                            autoplayVideos: prefs.settings.autoplay_videos,
                            loopVideos: prefs.settings.loop_videos,
                            muteOnOpen: prefs.settings.mute_on_open,
                            autoExpandCw: prefs.settings.auto_expand_cw,
                            appearance: prefs.settings.appearance,
                        }));
                    }
                } catch (error) {
                    console.error('Failed to sync preferences from server:', error);
                }
            },

            /**
             * Updates a preference both locally and on the server
             */
            updatePreference: async (key: string, value: any) => {
                set((state) => ({
                    ...state,
                    [key]: value,
                }));

                const keyMap: Record<string, string> = {
                    hideForYouFeed: 'hide_for_you_feed',
                    defaultFeed: 'default_feed',
                    autoplayVideos: 'autoplay_videos',
                    loopVideos: 'loop_videos',
                    muteOnOpen: 'mute_on_open',
                    autoExpandCw: 'auto_expand_cw',
                    appearance: 'appearance',
                };

                const apiKey = keyMap[key] || key;

                try {
                    await updatePreferences({ [apiKey]: value });
                } catch (error) {
                    console.error('Failed to update preference on server:', error);
                }
            },

            setDesktopVideoMuted: (value: boolean) => {
                // Keep this device preference independent of the server account.
                Storage.set('desktopVideoMuted', value);
                set((state) => ({ ...state, desktopVideoMuted: value }));
            },

            setHideForYouFeed: async (value: boolean) => {
                await get().updatePreference('hideForYouFeed', value);
            },

            setDefaultFeed: async (feed: 'following' | 'local' | 'forYou') => {
                await get().updatePreference('defaultFeed', feed);
            },

            setUser: (user: LoopsUser, server: string) => {
                set((state) => ({
                    ...state,
                    user,
                    server,
                    isLoggedIn: true,
                }));
            },

            clearUser: () => {
                set((state) => ({
                    ...state,
                    user: null,
                    server: null,
                    isLoggedIn: false,
                }));
            },

            logOut: async (onComplete?: () => void) => {
                try {
                    const server = Storage.getString('app.instance');
                    const token = Storage.getString('app.token');

                    if (server && token) {
                        await fetch(`https://${server}/api/v1/app/logout`, {
                            method: 'POST',
                            headers: {
                                Authorization: `Bearer ${token}`,
                                'Content-Type': 'application/json',
                                Accept: 'application/json',
                            },
                        });
                    }
                } catch (error) {
                    console.warn('Server logout request failed:', error);
                }

                useNotificationStore.getState().resetBadgeCount();
                queryClient.removeQueries({ queryKey: ['videos'] });

                OAuthService.logout();

                set({
                    isLoggedIn: false,
                    shouldCreateAccount: false,
                    hasCompletedOnboarding: false,
                    _hasHydrated: true,
                    user: null,
                    server: null,
                    hideForYouFeed: false,
                    defaultFeed: 'local',
                    autoplayVideos: true,
                    loopVideos: true,
                    muteOnOpen: false,
                    autoExpandCw: false,
                    appearance: 'light',
                });

                universalStorage.removeItem('auth-store.v0.5').catch((error) => {
                    console.error('Failed to clear persisted auth store:', error);
                });
            },

            completeOnboarding: () => {
                set((state) => ({
                    ...state,
                    hasCompletedOnboarding: true,
                }));
            },

            resetOnboarding: () => {
                set((state) => ({
                    ...state,
                    hasCompletedOnboarding: false,
                }));
            },

            setHasHydrated: (value: boolean) => {
                set((state) => ({
                    ...state,
                    _hasHydrated: value,
                }));

                if (value) {
                    setAuthFailureHandler((reason) => {
                        get().logOut();
                        Alert.alert(
                            'Session Expired',
                            reason || 'Your session is no longer valid. Please log in again.',
                        );
                    });

                    get().syncAuthState();
                    if (get().isLoggedIn) {
                        get().syncPreferencesFromServer();
                    }
                }
            },
        }),
        {
            name: 'auth-store.v0.5',
            storage: createJSONStorage(() => universalStorage),
            onRehydrateStorage: () => {
                return (state, error) => {
                    if (error) {
                        console.error('Failed to rehydrate auth store:', error);
                    }
                    state?.setHasHydrated(true);
                };
            },

            partialize: (state) => ({
                shouldCreateAccount: state.shouldCreateAccount,
                hasCompletedOnboarding: state.hasCompletedOnboarding,
                hideForYouFeed: state.hideForYouFeed,
                defaultFeed: state.defaultFeed,
                autoplayVideos: state.autoplayVideos,
                loopVideos: state.loopVideos,
                muteOnOpen: state.muteOnOpen,
                desktopVideoMuted: state.desktopVideoMuted,
                autoExpandCw: state.autoExpandCw,
                appearance: state.appearance,
            }),
        },
    ),
);
