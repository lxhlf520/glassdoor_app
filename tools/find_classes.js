Java.perform(function() {
    var all = Java.enumerateLoadedClassesSync();
    var filtered = all.filter(function(c) {
        var lc = c.toLowerCase();
        return lc.indexOf('okhttp') !== -1 || lc.indexOf('retrofit') !== -1 || lc.indexOf('apollo') !== -1 || lc.indexOf('glassdoor') !== -1;
    });
    send({total: all.length, matched: filtered.length, classes: filtered.slice(0, 200)});
});
