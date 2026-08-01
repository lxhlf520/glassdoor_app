Java.perform(function() {
    var cls = Java.use('com.apollographql.apollo.network.http.HttpNetworkTransport$execute$1');
    var fields = cls.class.getDeclaredFields().map(function(f) { return f.toString(); });
    send({fields: fields});
});
