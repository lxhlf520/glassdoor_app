Java.perform(function() {
    var all = Java.enumerateLoadedClassesSync();
    var filters = ['okhttp3','ktor','io.ktor','com.apollographql.apollo.network.http.HttpNetworkTransport','java.net.HttpURLConnection'];
    var filtered = all.filter(function(c) {
        var lc = c.toLowerCase();
        return filters.some(function(f){ return lc.indexOf(f.toLowerCase()) !== -1; });
    });
    send({matched: filtered.length, classes: filtered.slice(0, 300)});
});
