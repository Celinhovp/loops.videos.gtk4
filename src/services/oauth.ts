import { Storage } from '@/utils/cache';
import { get, loginPreflightCheck, postForm } from '@/utils/requests';
import * as Linking from 'expo-linking';
import * as WebBrowser from 'expo-web-browser';
import { Alert, Platform } from 'react-native';
import { queryClient } from '../lib/queryClient';
import { fetchServerConfig, serverConfigQueryKey } from '../services/config';
import { useConfigStore } from '../stores/configStore';

WebBrowser.maybeCompleteAuthSession();

export interface OAuthApp {
    client_id: string;
    client_secret: string;
    instance: string;
    client_name: string;
    redirect_uris?: string | string[];
    redirect_uri?: string;
}

export interface OAuthToken {
    access_token: string;
    refresh_token: string;
    token_type: string;
    scope: string;
    created_at: number;
    expires_in: number;
}

export interface LoopsUser {
    id: string;
    username: string;
    acct: string;
    display_name: string;
    avatar: string;
    header?: string;
    note?: string;
    followers_count: number;
    following_count: number;
    statuses_count: number;
    [key: string]: any;
}

export class OAuthService {
    private static readonly APP_NAME = `Loops for ${Platform.OS === 'android' ? 'Android' : 'iOS'}`;
    private static readonly DEFAULT_SCOPES = 'user:read user:write video:create video:read';
    private static lastLoginError: string | null = null;

    static getLastLoginError(): string | null {
        return this.lastLoginError;
    }

    private static getRedirectUri(): string {
        return Linking.createURL('oauth-callback');
    }

    private static isGtkDesktopWebView(): boolean {
        return (
            Platform.OS === 'web' &&
            typeof window !== 'undefined' &&
            /Loops\/\d+.*GTK4/.test(window.navigator.userAgent)
        );
    }

    private static async waitForDesktopOAuthCallback(): Promise<string | null> {
        const deadline = Date.now() + 10 * 60 * 1000;
        const statusUrl = `${window.location.origin}/oauth-callback-status`;

        while (Date.now() < deadline) {
            await new Promise((resolve) => setTimeout(resolve, 1000));
            try {
                const response = await fetch(statusUrl, { cache: 'no-store' });
                if (!response.ok) continue;
                const result = await response.json();
                if (typeof result.url === 'string' && result.url) return result.url;
            } catch (error) {
                console.warn('Waiting for desktop OAuth callback:', error);
            }
        }

        return null;
    }

    /**
     * Initiates the registration flow via WebBrowser
     */
    static async registerWithWebBrowser(server: string): Promise<boolean> {
        try {
            const url = `https://${server}`;

            const REDIRECT_URI = Linking.createURL('register-callback');

            const registerUrl = `${url}/auth/app/register?mobile=true&redirect_uri=${encodeURIComponent(REDIRECT_URI)}`;

            const authResult = await WebBrowser.openAuthSessionAsync(registerUrl, REDIRECT_URI, {
                showInRecents: true,
                createTask: false,
            });

            if (authResult.type === 'success') {
                return await this.handleRegistrationCallback(authResult.url, server);
            } else if (authResult.type === 'cancel') {
                return false;
            } else {
                Alert.alert('Error', 'Registration was unsuccessful. Please try again.');
                return false;
            }
        } catch (error) {
            Alert.alert('Error', 'An error occurred during registration. Please try again.');
            return false;
        }
    }

    /**
     * Handles the registration callback with token
     */
    private static async handleRegistrationCallback(url: string, server: string): Promise<boolean> {
        try {
            console.log('Handling registration callback URL:', url);

            const { queryParams } = Linking.parse(url);

            if (queryParams?.cancelled === 'true') {
                console.log('Registration was cancelled');
                return false;
            }

            if (queryParams?.error) {
                Alert.alert('Error', queryParams.error as string);
                return false;
            }

            const token = queryParams?.token as string;
            const userJson = queryParams?.user as string;

            if (!token || !userJson) {
                Alert.alert('Error', 'Registration data not received.');
                return false;
            }

            let user: LoopsUser;
            try {
                user = JSON.parse(userJson);
            } catch (error) {
                console.error('Failed to parse user data:', error);
                Alert.alert('Error', 'Invalid user data received.');
                return false;
            }

            Storage.set('user.token', token);
            Storage.set('user.profile', JSON.stringify(user));
            Storage.set('user.server', server);
            Storage.set('app.instance', server);
            Storage.set('app.token', token);

            console.log('Registration successful for user:', user.username);

            return true;
        } catch (error) {
            console.error('Registration callback error:', error);
            Alert.alert('Error', 'Failed to complete registration. Please try again.');
            return false;
        }
    }

    /**
     * Initiates the OAuth login flow
     */
    static async login(server: string, enabledScopes?: string): Promise<boolean> {
        this.lastLoginError = null;
        try {
            const scopes = enabledScopes || this.DEFAULT_SCOPES;

            const precheck = await loginPreflightCheck(server);
            if (!precheck) {
                Alert.alert(
                    'Error',
                    'Unable to connect to server. Please check the URL and try again.',
                );
                return false;
            }

            const url = `https://${server}`;
            const REDIRECT_URI = this.getRedirectUri();

            console.log('OAuth Redirect URI:', REDIRECT_URI);

            const app = await this.registerApp(url, REDIRECT_URI, scopes);
            if (!app) {
                Alert.alert('Error', 'Failed to register application with server.');
                return false;
            }

            this.storeAppCredentials(app, REDIRECT_URI);

            const authUrl =
                `${url}/oauth/authorize` +
                `?client_id=${app.client_id}` +
                `&scope=${scopes.split(' ').join('+')}` +
                `&redirect_uri=${encodeURIComponent(REDIRECT_URI)}` +
                `&response_type=code`;

            console.log('Opening auth URL:', authUrl);

            // Desktop/web builds can block a popup opened after the preflight and
            // client registration requests. Navigate in the current WebView instead;
            // the instance redirects back to /oauth-callback after authorization.
            if (Platform.OS === 'web' && typeof window !== 'undefined') {
                window.location.assign(authUrl);
                if (this.isGtkDesktopWebView()) {
                    const callbackUrl = await this.waitForDesktopOAuthCallback();
                    if (!callbackUrl) {
                        this.lastLoginError =
                            'O navegador terminou a autorização, mas o app não recebeu o retorno local. Verifique se a instância do Loops continuou aberta.';
                        return false;
                    }
                    return await this.handleCallback(callbackUrl);
                }
                return new Promise<boolean>(() => {});
            }

            const authResult = await WebBrowser.openAuthSessionAsync(authUrl, REDIRECT_URI, {
                showInRecents: true,
                createTask: false,
            });

            console.log('Auth result:', authResult);

            if (authResult.type === 'success') {
                return await this.handleCallback(authResult.url);
            } else if (authResult.type === 'cancel') {
                console.log('User cancelled OAuth flow');
                return false;
            } else {
                Alert.alert('Error', 'Authentication was unsuccessful. Please try again.');
                return false;
            }
        } catch (error) {
                this.lastLoginError = error instanceof Error ? error.message : 'Falha ao iniciar a autorização.';
                console.error('OAuth login error:', this.lastLoginError);
            Alert.alert('Error', 'An error occurred during login. Please try again.');
            return false;
        }
    }

    /**
     * Registers the app with the Loops instance
     */
    private static async registerApp(
        instanceUrl: string,
        redirectUri: string,
        scopes: string,
    ): Promise<OAuthApp | null> {
        try {
            const formBody = {
                client_name: this.APP_NAME,
                redirect_uris: [redirectUri],
                scopes,
                website: 'https://joinloops.org',
            };

            console.log('Registering OAuth app with the Loops instance.');
            // Use the browser's multipart form request. Besides matching the
            // established Loops client flow, this avoids Cloudflare blocking
            // requests forwarded by a non-browser Flatpak helper.
            const response = await postForm(`${instanceUrl}/api/v1/apps`, formBody);
            const app = await response.json();
            const instance = instanceUrl.replace(/^https?:\/\//, '').replace(/\/$/, '');

            return {
                ...app,
                instance,
            };
        } catch (error) {
            this.lastLoginError = error instanceof Error
                ? error.message
                : 'Não foi possível cadastrar o app na instância Loops.';
            console.error('App registration error:', error);
            return null;
        }
    }

    /**
     * Stores app credentials securely
     */
    private static storeAppCredentials(app: OAuthApp, fallbackRedirectUri: string): void {
        Storage.set('app.client_id', app.client_id);
        Storage.set('app.client_secret', app.client_secret);
        Storage.set('app.instance', app.instance);
        Storage.set('app.name', app.client_name);
        const redirectUri = Array.isArray(app.redirect_uris)
            ? app.redirect_uris[0]
            : app.redirect_uris || app.redirect_uri || fallbackRedirectUri;
        Storage.set('app.redirect_uri', redirectUri);
    }

    /**
     * Handles the OAuth callback after user authorization
     */
    static async completeWebLogin(url: string): Promise<boolean> {
        return this.handleCallback(url);
    }

    private static async handleCallback(url: string): Promise<boolean> {
        try {
            console.log('Handling OAuth callback.');

            const { queryParams } = Linking.parse(url);

            if (queryParams?.message || queryParams?.error) {
                Alert.alert('Error', (queryParams.message || queryParams.error) as string);
                return false;
            }

            if (!queryParams?.code) {
                Alert.alert('Error', 'Authorization code not received.');
                return false;
            }

            const instance = Storage.getString('app.instance');
            const clientId = Storage.getString('app.client_id');
            const clientSecret = Storage.getString('app.client_secret');

            if (!instance || !clientId || !clientSecret) {
                throw new Error('O retorno chegou, mas as credenciais temporárias do app não foram encontradas. Reinicie a autorização.');
            }

            const api = `https://${instance}`;
            const REDIRECT_URI = Storage.getString('app.redirect_uri') || this.getRedirectUri();

            const token = await this.exchangeCodeForToken(
                api,
                clientId,
                clientSecret,
                REDIRECT_URI,
                queryParams.code as string,
            );

            if (!token) {
                this.lastLoginError ||= 'O servidor recusou a troca do código por um token. Veja a resposta HTTP exibida abaixo.';
                Alert.alert('Error', 'Failed to obtain access token.');
                return false;
            }

            this.storeTokenData(token);

            const user = await this.verifyCredentials(api, token.access_token);
            if (!user) {
                this.lastLoginError = 'O token foi recebido, mas o app não conseguiu confirmar o perfil da conta.';
                Alert.alert('Error', 'Failed to verify user credentials.');
                return false;
            }

            Storage.set('user.profile', JSON.stringify(user));
            Storage.set('user.server', instance);
            Storage.set('user.token', token.access_token);

            console.log('OAuth login successful for user:', user.username);

            await queryClient.prefetchQuery({
                queryKey: serverConfigQueryKey,
                queryFn: fetchServerConfig,
            });

            return true;
        } catch (error) {
            this.lastLoginError = error instanceof Error ? error.message : 'Falha ao concluir o retorno OAuth.';
            console.error('OAuth callback error:', this.lastLoginError);
            Alert.alert('Error', 'Failed to complete login. Please try again.');
            return false;
        }
    }

    /**
     * Exchanges authorization code for access token
     */
    private static async exchangeCodeForToken(
        apiUrl: string,
        clientId: string,
        clientSecret: string,
        redirectUri: string,
        code: string,
    ): Promise<OAuthToken | null> {
        try {
            const tokenRequestBody = {
                client_id: clientId,
                client_secret: clientSecret,
                redirect_uri: redirectUri,
                grant_type: 'authorization_code',
                code: code,
            };

            console.log('Exchanging code for token...');

            const gtkWebView = this.isGtkDesktopWebView();
            const instance = apiUrl.replace(/^https?:\/\//, '');
            const response = gtkWebView
                ? await fetch(`${window.location.origin}/oauth-token`, {
                      method: 'POST',
                      headers: {
                          Accept: 'application/json',
                          'Content-Type': 'application/json',
                      },
                      body: JSON.stringify({ ...tokenRequestBody, instance }),
                  })
                : await fetch(`${apiUrl}/oauth/token`, {
                      method: 'POST',
                      headers: {
                          Accept: 'application/json',
                          'Content-Type': 'application/json',
                      },
                      body: JSON.stringify(tokenRequestBody),
                  });

            if (!response.ok) {
                const body = await response.text();
                let detail = '';
                try {
                    const errorData = JSON.parse(body);
                    detail = errorData.error_description || errorData.error || errorData.message || errorData.detail || errorData.title || '';
                } catch {}
                if (!detail) {
                    detail = body.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 180);
                }
                this.lastLoginError = `A troca do código falhou (HTTP ${response.status})${detail ? `: ${detail}` : '.'}`;
                throw new Error(this.lastLoginError);
            }

            const token = await response.json();
            if (!token?.access_token) throw new Error('Token response did not contain an access token.');
            return token;
        } catch (error) {
            this.lastLoginError ||= error instanceof Error ? error.message : 'Falha ao trocar o código por token.';
            console.error('Token exchange error:', this.lastLoginError);
            return null;
        }
    }

    /**
     * Stores OAuth token data
     */
    private static storeTokenData(token: OAuthToken): void {
        Storage.set('app.token', token.access_token);
        Storage.set('app.refresh_token', token.refresh_token);
        Storage.set('app.expires_in', token.expires_in?.toString());
    }

    /**
     * Verifies user credentials and fetches profile
     */
    private static async verifyCredentials(
        apiUrl: string,
        accessToken: string,
    ): Promise<LoopsUser | null> {
        try {
            const response = await get(`${apiUrl}/api/v1/account/info/self`, accessToken);
            const user = await response.json();
            return user.data;
        } catch (error) {
            this.lastLoginError = error instanceof Error
                ? `O token foi recebido, mas a validação da conta falhou: ${error.message}`
                : 'O token foi recebido, mas a validação da conta falhou.';
            console.error('Credential verification error:', this.lastLoginError);
            return null;
        }
    }

    /**
     * Refreshes the access token using refresh token
     */
    static async refreshToken(): Promise<boolean> {
        try {
            const instance = Storage.getString('app.instance');
            const clientId = Storage.getString('app.client_id');
            const clientSecret = Storage.getString('app.client_secret');
            const refreshToken = Storage.getString('app.refresh_token');

            if (!instance || !clientId || !clientSecret || !refreshToken) {
                return false;
            }

            const api = `https://${instance}`;

            const tokenRequestBody = {
                client_id: clientId,
                client_secret: clientSecret,
                grant_type: 'refresh_token',
                refresh_token: refreshToken,
            };

            const response = await postForm(`${api}/oauth/token`, tokenRequestBody);
            const token = await response.json();

            this.storeTokenData(token);
            Storage.set('user.token', token.access_token);

            return true;
        } catch (error) {
            console.error('Token refresh error:', error);
            return false;
        }
    }

    static async storeAppleAuthCredentials(token: string, user: LoopsUser, server: string): void {
        Storage.set('user.token', token);
        Storage.set('user.server', server);
        Storage.set('app.instance', server);
        Storage.set('app.token', token);

        const api = `https://${server}`;
        const userRes = await this.verifyCredentials(api, token);
        Storage.set('user.profile', JSON.stringify(userRes));
    }

    /**
     * Logs out the user by clearing all stored credentials
     */
    static logout(): void {
        queryClient.removeQueries({ queryKey: serverConfigQueryKey });
        useConfigStore.getState().setConfig(null);

        Storage.remove('app.client_id');
        Storage.remove('app.client_secret');
        Storage.remove('app.instance');
        Storage.remove('app.name');
        Storage.remove('app.redirect_uri');
        Storage.remove('app.token');
        Storage.remove('app.refresh_token');
        Storage.remove('app.expires_in');

        Storage.remove('user.profile');
        Storage.remove('user.server');
        Storage.remove('user.token');
    }

    /**
     * Gets the current user profile from storage
     */
    static getCurrentUser(): LoopsUser | null {
        const profileJson = Storage.getString('user.profile');
        if (!profileJson) return null;

        try {
            return JSON.parse(profileJson);
        } catch {
            return null;
        }
    }

    /**
     * Gets the current access token
     */
    static getAccessToken(): string | null {
        return Storage.getString('user.token') || Storage.getString('app.token') || null;
    }

    /**
     * Gets the current server instance
     */
    static getCurrentServer(): string | null {
        return Storage.getString('user.server') || Storage.getString('app.instance') || null;
    }

    /**
     * Checks if user is currently authenticated
     */
    static isAuthenticated(): boolean {
        const token = this.getAccessToken();
        const user = this.getCurrentUser();
        return !!(token && user);
    }
}
