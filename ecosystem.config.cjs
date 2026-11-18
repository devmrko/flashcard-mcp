// pm2 start ecosystem.config.cjs && pm2 save
module.exports = {
  apps: [{
    name: "flashcard",
    cwd: __dirname,
    script: ".venv/bin/flashcard",
    args: "serve",
    interpreter: "none",
    autorestart: true,
    max_restarts: 10,
  }],
};
