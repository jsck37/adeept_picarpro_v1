#!/usr/bin/env python3
"""PiCar Pro Flask app — HTTP + MJPEG + REST API.

Routes are organised into Blueprint modules under Server/routes/ for
readability and maintainability.
"""

import os
from flask import Flask

from Server.routes.static_routes import create_static_blueprint
from Server.routes.video_routes import create_video_blueprint
from Server.routes.command_routes import create_command_blueprint
from Server.routes.api_routes import create_api_blueprint
from Server.routes.bluetooth_routes import create_bluetooth_blueprint


def create_app(state):
    server_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(server_dir, "dist")
    docs_dir = os.path.join(os.path.dirname(server_dir), "docs")

    app = Flask(__name__, template_folder=dist_dir, static_folder=None)
    app.config['SECRET_KEY'] = 'picarpro'

    # ── CORS ──────────────────────────────────────────────────────────

    @app.after_request
    def cors(r):
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    # ── Register blueprints ───────────────────────────────────────────

    app.register_blueprint(create_static_blueprint(dist_dir, docs_dir))
    app.register_blueprint(create_video_blueprint(state))
    app.register_blueprint(create_command_blueprint(state))
    app.register_blueprint(create_api_blueprint(state))
    app.register_blueprint(create_bluetooth_blueprint(state))

    return app
