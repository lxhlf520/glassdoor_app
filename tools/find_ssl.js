var mods = Process.enumerateModules();
var hits = [];
mods.forEach(function(m) {
    try {
        var exports = Module.enumerateExports(m.name);
        exports.forEach(function(e) {
            if (e.name.indexOf('SSL_write') !== -1 || e.name.indexOf('SSL_read') !== -1 || e.name.indexOf('send') !== -1 || e.name.indexOf('recv') !== -1) {
                hits.push({module: m.name, name: e.name, address: e.address.toString()});
            }
        });
    } catch (e) {}
});
send({hits: hits.slice(0, 100)});
