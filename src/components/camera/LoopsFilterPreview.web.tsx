import type { ViewStyle } from 'react-native';

// Live camera filters are provided by a native view and are unavailable in the
// GTK/WebKit desktop build. Keep the upload flow usable without that preview.
export function LoopsFilterPreview(_props: { style?: ViewStyle }) {
    return null;
}
