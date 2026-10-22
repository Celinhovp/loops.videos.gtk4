const React = require('react');
const { Text } = require('react-native');

function UITextView(props) {
    return React.createElement(Text, props);
}

module.exports = {
    UITextView,
    default: UITextView,
};
