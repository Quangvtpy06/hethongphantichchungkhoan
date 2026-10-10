/* Giữ trục giá vừa vùng đang xem; thao tác tại trình duyệt, không gọi lại dữ liệu. */
(() => {
    const DAY = 86400000;
    const dateValue = value => typeof value === 'number' ? value : Date.parse(value);
    function chart() { return document.querySelector('#technical-chart .js-plotly-plot'); }
    function fitPrice(gd) {
        const range = gd._fullLayout?.xaxis?.range;
        if (!range) return;
        const [start, end] = range.map(dateValue);
        const prices = [], volumes = [];
        (gd.data || []).forEach(trace => {
            if (trace.visible === 'legendonly' || trace.visible === false) return;
            const axis = trace.yaxis || 'y';
            if (axis !== 'y' && axis !== 'y2') return;
            (trace.x || []).forEach((x, i) => {
                const time = dateValue(x);
                if (time < start || time > end) return;
                const fields = trace.type === 'candlestick' ? ['low', 'high'] : ['y'];
                fields.forEach(field => {
                    const v = trace[field]?.[i];
                    if (typeof v === 'number' && Number.isFinite(v)) (axis === 'y' ? prices : volumes).push(v);
                });
            });
        });
        if (!prices.length) return;
        prices.push(...(gd.layout.meta?.priceLevels || []));
        const low = Math.min(...prices), high = Math.max(...prices);
        const pad = Math.max((high - low) * .08, high * .005);
        const updates = {'yaxis.range': [low-pad, high+pad]};
        if (volumes.length) updates['yaxis2.range'] = [0, Math.max(1, ...volumes) * 1.12];
        window.Plotly.relayout(gd, updates);
    }
    function bind() {
        const gd = chart();
        if (!gd || !gd.on || gd.__technicalBound) return;
        gd.__technicalBound = true;
        let pending;
        gd.on('plotly_relayout', event => {
            if (!Object.keys(event).some(key => /^xaxis\d*\.(range|autorange)/.test(key))) return;
            clearTimeout(pending);
            pending = setTimeout(() => { if (gd.isConnected) fitPrice(gd); }, 50);
        });
        gd.on('plotly_restyle', () => fitPrice(gd));
    }
    let scheduled = false;
    new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => { scheduled = false; bind(); });
    }).observe(document.documentElement, {childList: true, subtree: true});
    document.addEventListener('click', event => {
        const id = event.target.closest('.technical-chart-controls button')?.id;
        const gd = chart();
        if (!id || !gd?._fullLayout?.xaxis) return;
        const axis = gd._fullLayout.xaxis;
        let [left, right] = axis.range.map(dateValue);
        const span = right-left, center = (left+right)/2;
        if (id === 'chart-reset') [left, right] = gd.layout.meta.initialRange.map(dateValue);
        if (id === 'chart-previous') { left -= span*.5; right -= span*.5; }
        if (id === 'chart-next') { left += span*.5; right += span*.5; }
        if (id === 'chart-zoom-in' || id === 'chart-zoom-out') {
            const width = Math.max(3*DAY, span*(id === 'chart-zoom-in' ? .65 : 1.5));
            left = center-width/2; right = center+width/2;
        }
        const min = dateValue(axis.minallowed), max = dateValue(axis.maxallowed);
        if (left < min) { right += min-left; left = min; }
        if (right > max) { left -= right-max; right = max; }
        left = Math.max(left, min);
        const range = [new Date(left).toISOString(), new Date(right).toISOString()];
        const update = {};
        ['xaxis', 'xaxis2', 'xaxis3'].forEach(name => {
            update[name+'.range'] = range;
            update[name+'.autorange'] = false;
        });
        window.Plotly.relayout(gd, update);
    });
    bind();
})();
