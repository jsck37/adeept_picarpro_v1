#!/usr/bin/env python3
"""Static file serving blueprint — index, assets, and docs."""

import os
from flask import Blueprint, render_template, send_from_directory


def create_static_blueprint(dist_dir, docs_dir):
    """Return a Blueprint that serves the SPA front-end and docs JSON.

    Parameters
    ----------
    dist_dir : str
        Absolute path to the ``dist/`` directory containing built front-end
        assets (index.html, style.css, app.js, …).
    docs_dir : str
        Absolute path to the ``docs/`` directory containing JSON documentation
        files (index.json, pinout.json).
    """
    bp = Blueprint("static", __name__)

    @bp.route("/")
    def index():
        return render_template("index.html")

    @bp.route("/favicon.ico")
    def favicon():
        return "", 204

    @bp.route("/style.css")
    def css():
        return send_from_directory(dist_dir, "style.css", mimetype="text/css")

    @bp.route("/app.js")
    def js():
        return send_from_directory(dist_dir, "app.js", mimetype="application/javascript")

    @bp.route("/<path:fn>")
    def dist_files(fn):
        fp = os.path.join(dist_dir, fn)
        if os.path.isfile(fp):
            return send_from_directory(dist_dir, fn)
        return "", 404

    @bp.route("/docs/index.json")
    def docs_index():
        return send_from_directory(docs_dir, "index.json", mimetype="application/json")

    @bp.route("/docs/pinout.json")
    def docs_pinout():
        return send_from_directory(docs_dir, "pinout.json", mimetype="application/json")

    return bp
