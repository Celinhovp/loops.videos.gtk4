import { Platform } from 'react-native';
import { createMMKV } from 'react-native-mmkv';

class WebStorage {
    private mem = new Map<string, string>();

    getString(key: string): string | undefined {
        try {
            if (typeof localStorage !== 'undefined') {
                const val = localStorage.getItem(key);
                return val !== null ? val : undefined;
            }
        } catch {}
        return this.mem.get(key);
    }

    set(key: string, value: string | boolean | number): void {
        const str = String(value);
        try {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem(key, str);
            }
        } catch {}
        this.mem.set(key, str);
    }

    getBoolean(key: string): boolean | undefined {
        const val = this.getString(key);
        return val !== undefined ? val === 'true' : undefined;
    }

    getNumber(key: string): number | undefined {
        const val = this.getString(key);
        return val !== undefined ? Number(val) : undefined;
    }

    delete(key: string): void {
        this.remove(key);
    }

    remove(key: string): void {
        try {
            if (typeof localStorage !== 'undefined') {
                localStorage.removeItem(key);
            }
        } catch {}
        this.mem.delete(key);
    }

    clearAll(): void {
        try {
            if (typeof localStorage !== 'undefined') {
                localStorage.clear();
            }
        } catch {}
        this.mem.clear();
    }

    contains(key: string): boolean {
        return this.getString(key) !== undefined;
    }

    getAllKeys(): string[] {
        try {
            if (typeof localStorage !== 'undefined') {
                return Object.keys(localStorage);
            }
        } catch {}
        return Array.from(this.mem.keys());
    }
}

export const Storage =
    Platform.OS === 'web' || typeof window === 'undefined'
        ? (new WebStorage() as any)
        : createMMKV();
