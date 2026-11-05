const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const html = readFileSync(path.join(__dirname, '../templates/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].split('// Initialize Theme & Stats on Boot')[0];
function editor(fetch) {
    const alerts = [];
    const context = vm.createContext({
        document: {getElementById: () => null},
        window: {location: {}},
        fetch, alert: message => alerts.push(message),
    });
    vm.runInContext(script, context);
    context.updateStats = () => {};
    return {context, alerts, run: source => vm.runInContext(source, context)};
}
const response = (ok = true, disk_saved = true) => ({ok, json: async () => ({disk_saved, stats: {}, detail: 'Invalid correction', save_message: 'Disk unavailable'})});

test('WIPS download waits for the typed correction and sends a numeric column index', async () => {
    let finish;
    const requests = [];
    const e = editor((url, options) => {
        requests.push(JSON.parse(options.body));
        return new Promise(resolve => { finish = resolve; });
    });
    const exporting = e.run("rememberCellEdit(42, 12, '19900101'); exportHeaderlessPirl()");
    assert.equal(e.context.window.location.href, undefined);
    assert.deepEqual(requests, [{pirl_row_index: 42, col_index: 12, new_value: '19900101'}]);
    finish(response());
    await exporting;
    assert.equal(e.context.window.location.href, '/api/export-headerless-pirl');
});

test('a value changed during a save is also persisted before export', async () => {
    let finish;
    const requests = [];
    const e = editor((url, options) => {
        requests.push(JSON.parse(options.body));
        return requests.length === 1 ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(response());
    });
    const exporting = e.run("rememberCellEdit(42, 12, '19900101'); exportHeaderlessPirl()");
    e.run("rememberCellEdit(42, 12, '19900202')");
    finish(response());
    await exporting;
    assert.deepEqual(requests.map(r => r.new_value), ['19900101', '19900202']);
    assert.equal(e.context.window.location.href, '/api/export-headerless-pirl');
});

test('an unsuccessful correction blocks exporting stale data', async () => {
    const e = editor(async () => response(false));
    await e.run("rememberCellEdit(42, 47, '9'); exportHeaderlessPirl()");
    assert.equal(e.context.window.location.href, undefined);
    assert.equal(e.run('pendingCellEdits.size'), 1);
    assert.match(e.alerts[0], /Invalid correction/);
});

test('Save to File commits typed fields before the file save request', async () => {
    const calls = [];
    const e = editor(async url => {
        calls.push(url);
        return url === '/api/save-file'
            ? {ok: true, json: async () => ({status:'success', message:'Saved to /tmp/corrected.csv', stats:{}})}
            : response();
    });
    await e.run("rememberCellEdit(42, 12, '19900101'); saveFileToFolder()");
    assert.deepEqual(calls, ['/api/cell-update', '/api/save-file']);
    assert.match(e.alerts[0], /\/tmp\/corrected.csv/);
});
