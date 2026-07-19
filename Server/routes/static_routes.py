import os
from flask import Blueprint, render_template, send_from_directory


def create_static_blueprint(dist_dir, docs_dir):
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

    @bp.route("/docs/components/<path:fn>")
    def docs_component(fn):
        comp_dir = os.path.join(docs_dir, "components")
        fp = os.path.join(comp_dir, fn)
        if os.path.isfile(fp):
            return send_from_directory(comp_dir, fn, mimetype="application/json")
        return "", 404

    return bp
