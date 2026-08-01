Java.perform(function() {
    var HttpURLConnection = Java.use('java.net.HttpURLConnection');
    var URL = Java.use('java.net.URL');

    URL.toString.implementation = function() {
        return this.getProtocol().value + '://' + this.getHost().value + this.getFile().value;
    };

    HttpURLConnection.setRequestProperty.overload('java.lang.String', 'java.lang.String').implementation = function(key, value) {
        if (this.getURL().toString().indexOf('glassdoor') !== -1 || key.toLowerCase().indexOf('gd') !== -1) {
            send({type: 'header', url: this.getURL().toString(), key: key, value: value});
        }
        return this.setRequestProperty(key, value);
    };

    HttpURLConnection.connect.implementation = function() {
        var url = this.getURL().toString();
        if (url.indexOf('glassdoor') !== -1) {
            send({type: 'connect', url: url, method: this.getRequestMethod()});
        }
        return this.connect();
    };

    HttpURLConnection.getResponseCode.implementation = function() {
        var code = this.getResponseCode();
        var url = this.getURL().toString();
        if (url.indexOf('glassdoor') !== -1) {
            send({type: 'response', url: url, code: code});
        }
        return code;
    };

    send({status: 'hooks installed'});
});
