Java.perform(function() {
    var L = Java.use('com.apollographql.apollo.network.http.HttpNetworkTransport$execute$1');
    L.$init.implementation = function(transport, httpRequest, request, scalarAdapters, cont) {
        try {
            var info = {
                type: 'init',
                requestClass: request ? request.getClass().getName() : null,
                request: request ? request.toString() : null,
                httpRequestClass: httpRequest ? httpRequest.getClass().getName() : null,
                httpRequest: httpRequest ? httpRequest.toString() : null,
            };
            send(info);
        } catch (e) {
            send({type: 'init-error', error: String(e)});
        }
        return this.$init(transport, httpRequest, request, scalarAdapters, cont);
    };

    L.invokeSuspend.implementation = function(obj) {
        try {
            var req = this.$request.value;
            var httpReq = this.$httpRequest.value;
            send({
                type: 'invokeSuspend',
                request: req ? req.toString() : null,
                httpRequest: httpReq ? httpReq.toString() : null,
            });
        } catch (e) {
            send({type: 'invoke-error', error: String(e)});
        }
        return this.invokeSuspend(obj);
    };

    send({status: 'apollo hooks installed'});
});
