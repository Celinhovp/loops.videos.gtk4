import { Storage } from '@/utils/cache';
import React, { PropsWithChildren, createContext, useContext, useEffect } from 'react';
import tw, { useAppColorScheme, useDeviceContext } from 'twrnc';

export const storage = Storage;

type ColorScheme = 'light' | 'dark' | 'device';

type ThemeContextType = {
    colorScheme: ColorScheme;
    toggleTheme: () => void;
    setColorScheme: (scheme: ColorScheme) => void;
};

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export const ThemeProvider = (props: PropsWithChildren) => {
    useDeviceContext(tw, {
        observeDeviceColorSchemeChanges: false,
        initialColorScheme: (storage.getString('colorScheme') as ColorScheme) || 'light',
    });

    const [colorScheme, , setTwColorScheme] = useAppColorScheme(tw);

    const setColorScheme = (scheme: ColorScheme) => {
        // Save synchronously on web so a fast app close cannot lose the choice.
        storage.set('colorScheme', scheme);
        setTwColorScheme(scheme);
    };

    const toggleTheme = () => {
        setColorScheme(colorScheme === 'dark' ? 'light' : 'dark');
    };

    useEffect(() => {
        if (colorScheme) {
            storage.set('colorScheme', colorScheme as string);
        }
    }, [colorScheme]);

    return (
        <ThemeContext.Provider
            key={tw.memoBuster}
            {...props}
            value={{ colorScheme: colorScheme as ColorScheme, toggleTheme, setColorScheme }}
        />
    );
};

export const useTheme = () => {
    const context = useContext(ThemeContext);

    if (!context) {
        throw new Error('useTheme must be used within a ThemeProvider');
    }

    return context;
};
