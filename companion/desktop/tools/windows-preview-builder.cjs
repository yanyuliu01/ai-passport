const { join } = require('node:path');
const root = join(__dirname, '..');
const base = require('../package.json').build;
module.exports = { ...base, extends: null,
  electronDist: join(root, '.build-cache/electron-win'),
  extraResources: [{ from: join(root, 'runtime-dist-win'), to: 'runtime' }],
  win: { ...base.win, signAndEditExecutable: false },
};
