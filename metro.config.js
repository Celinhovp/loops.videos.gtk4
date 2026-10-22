const path = require('path');
const { getDefaultConfig } = require('expo/metro-config');

/** @type {import('expo/metro-config').MetroConfig} */
const config = getDefaultConfig(__dirname);

const defaultResolveRequest = config.resolver.resolveRequest;

config.resolver.resolveRequest = (context, moduleName, platform) => {
    if (platform === 'web') {
        if (moduleName === '@api.video/react-native-livestream') {
            return {
                filePath: path.resolve(__dirname, 'src/mocks/apiVideoMock.js'),
                type: 'sourceFile',
            };
        }
        if (moduleName === 'react-native-vision-camera') {
            return {
                filePath: path.resolve(__dirname, 'src/mocks/visionCameraMock.js'),
                type: 'sourceFile',
            };
        }
        if (moduleName === 'react-native-uitextview') {
            return {
                filePath: path.resolve(__dirname, 'src/mocks/uiTextViewMock.js'),
                type: 'sourceFile',
            };
        }
    }
    if (defaultResolveRequest) {
        return defaultResolveRequest(context, moduleName, platform);
    }
    return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
