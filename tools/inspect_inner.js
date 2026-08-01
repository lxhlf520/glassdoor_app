Java.perform(function() {
    var names = [
        'com.apollographql.apollo.network.http.HttpNetworkTransport$execute$1',
        'com.apollographql.apollo.network.http.HttpNetworkTransport$execute$1$a',
        'com.apollographql.apollo.network.http.a',
        'com.apollographql.apollo.network.http.c',
        'com.apollographql.apollo.network.http.d',
    ];
    names.forEach(function(n) {
        try {
            var cls = Java.use(n);
            var methods = cls.class.getDeclaredMethods().map(function(m){ return m.toString(); });
            send({class: n, ok: true, methods: methods});
        } catch (e) {
            send({class: n, ok: false, error: String(e)});
        }
    });
});
