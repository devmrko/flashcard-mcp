// Keep flashcard running with pm2:  pm2 start deploy/examples/pm2.config.cjs && pm2 save
const path = require("path");
module.exports = {
  apps: [{
    name: "flashcard",
    cwd: path.join(__dirname, "..", ".."),
    script: ".venv/bin/flashcard",
    args: "serve",
    interpreter: "none",
    autorestart: true,
    max_restarts: 10,
  }],
};
