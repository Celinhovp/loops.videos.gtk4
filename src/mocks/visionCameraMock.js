const React = require('react');
const { View } = require('react-native');

function Camera(props) {
    return React.createElement(View, props);
}

module.exports = {
    Camera,
    useCameraDevices: () => ({}),
    useCameraDevice: () => ({}),
    useCameraFormat: () => null,
    useFrameProcessor: () => {},
    Templates: {},
    default: Camera,
};
