import { ThemeProvider, useTheme } from '@/contexts/ThemeContext';
import { useAuthStore } from '@/utils/authStore';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import * as Notifications from 'expo-notifications';
import { router, SplashScreen, Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import React, { useEffect } from 'react';
import { Platform, StyleSheet, Text, TouchableOpacity, View, useColorScheme } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { KeyboardProvider } from 'react-native-keyboard-controller';

if (Platform.OS !== 'web') {
    Notifications.setNotificationHandler({
        handleNotification: async () => ({
            shouldPlaySound: false,
            shouldSetBadge: false,
            shouldShowBanner: true,
            shouldShowList: true,
        }),
    });
    SplashScreen.preventAutoHideAsync().catch(() => {});
}

const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: 2 } },
});

function useNotificationObserver() {
    useEffect(() => {
        if (Platform.OS === 'web') return;
        function redirect(notification: Notifications.Notification) {
            const url = notification.request.content.data?.url;
            if (typeof url === 'string') {
                router.push(url);
            }
        }

        const response = Notifications.getLastNotificationResponse();
        if (response?.notification) {
            redirect(response.notification);
        }

        const subscription = Notifications.addNotificationResponseReceivedListener((response) => {
            redirect(response.notification);
        });

        return () => {
            subscription.remove();
        };
    }, []);
}

function AppContent() {
    const { isLoggedIn, shouldCreateAccount } = useAuthStore();
    const { colorScheme } = useTheme();
    const systemColorScheme = useColorScheme();

    useNotificationObserver();

    const getStatusBarStyle = () => {
        if (colorScheme === 'device') {
            return systemColorScheme === 'dark' ? 'light' : 'dark';
        }
        return colorScheme === 'dark' ? 'light' : 'dark';
    };

    return (
        <React.Fragment>
            <StatusBar style={getStatusBarStyle()} />
            <Stack>
                {/* OAuth returns here before the app has an authenticated session. */}
                <Stack.Screen name="oauth-callback" options={{ headerShown: false }} />

                <Stack.Protected guard={isLoggedIn}>
                    <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
                    <Stack.Screen name="private" options={{ headerShown: false }} />
                    <Stack.Screen name="modal" options={{ presentation: 'modal' }} />
                </Stack.Protected>

                <Stack.Protected guard={!isLoggedIn}>
                    <Stack.Screen
                        name="sign-in"
                        options={{ headerShown: false, gestureEnabled: false }}
                    />
                    <Stack.Protected guard={shouldCreateAccount}>
                        <Stack.Screen name="create-account" />
                    </Stack.Protected>
                </Stack.Protected>
            </Stack>
        </React.Fragment>
    );
}

export default function RootLayout() {
    const { _hasHydrated } = useAuthStore();

    useEffect(() => {
        SplashScreen.hideAsync().catch(() => {});
    }, [_hasHydrated]);

    if (!_hasHydrated && Platform.OS !== 'web') {
        return null;
    }

    return (
        <GestureHandlerRootView style={{ flex: 1 }}>
            <KeyboardProvider>
                <ThemeProvider>
                    <QueryClientProvider client={queryClient}>
                        <AppContent />
                    </QueryClientProvider>
                </ThemeProvider>
            </KeyboardProvider>
        </GestureHandlerRootView>
    );
}

export function ErrorBoundary({ error, retry }) {
    return (
        <View style={styles.errorScreen}>
            <Text style={styles.errorTitle}>Loops could not open</Text>
            <Text style={styles.errorMessage}>{error?.message || 'An unexpected error occurred.'}</Text>
            <TouchableOpacity onPress={retry} style={styles.retryButton}>
                <Text style={styles.retryText}>Try again</Text>
            </TouchableOpacity>
        </View>
    );
}

const styles = StyleSheet.create({
    errorScreen: {
        flex: 1,
        justifyContent: 'center',
        alignItems: 'center',
        padding: 24,
        backgroundColor: '#000',
    },
    errorTitle: {
        color: '#fff',
        fontSize: 22,
        fontWeight: '700',
        marginBottom: 12,
    },
    errorMessage: {
        color: '#bbb',
        fontSize: 14,
        textAlign: 'center',
        marginBottom: 20,
    },
    retryButton: {
        borderRadius: 20,
        backgroundColor: '#d4c900',
        paddingHorizontal: 22,
        paddingVertical: 11,
    },
    retryText: {
        color: '#000',
        fontWeight: '700',
    },
});

