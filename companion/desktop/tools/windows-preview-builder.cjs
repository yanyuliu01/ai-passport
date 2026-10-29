const { join } = require('node:path');
const root = join(__dirname, '..');
const base = require('../package.json').build;
module.exports = { ...base, extends: null,
  toolsets: { ...base.toolsets, nsis: "1.2.1" },
  electronDist: join(root, '.build-cache/electron-win'),
  extraResources: [{ from: join(root, 'runtime-dist-win'), to: 'runtime' },
    { from: join(root, 'agents-dist-win'), to: 'agent-tools' }],
  win: { ...base.win, signAndEditExecutable: false },
};
