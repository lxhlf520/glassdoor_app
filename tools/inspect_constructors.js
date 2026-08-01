Java.perform(function() {
    var cls = Java.use('com.apollographql.apollo.network.http.HttpNetworkTransport$execute$1');
    var ctors = cls.class.getDeclaredConstructors().map(function(c) { return c.toString(); });
    send({constructors: ctors});
});
