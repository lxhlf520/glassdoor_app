Java.perform(function() {
    var clsName = 'com.apollographql.apollo.network.http.HttpNetworkTransport';
    var cls = Java.use(clsName);
    var methods = cls.class.getDeclaredMethods().map(function(m) { return m.toString(); });
    send({class: clsName, methods: methods});
});
