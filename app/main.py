# -*- coding: utf-8 -*-
from flask import Flask, g, request, render_template, redirect, url_for
from uuid import uuid4
import os
import json
import rethinkdb as r
from rethinkdb.errors import RqlRuntimeError, RqlDriverError

import downloadfonts
import models
from glyphpalette import fonts_all_glyphs
import init_db
from utils import filename_to_family_name
from settings import DIFF_LIMIT, VIEWS


__version__ = 2.101

app = Flask(__name__, static_url_path='/static')

RDB_HOST = os.environ.get('RDB_HOST') or 'localhost'
RDB_PORT = os.environ.get('RDB_PORT') or 28015
DB = 'diffenator_web'

init_db.build_tables(host=RDB_HOST, port=RDB_PORT, db=DB)


@app.before_request
def before_request():
    try:
        g.rdb_conn = r.connect(host=RDB_HOST, port=RDB_PORT, db=DB)
    except RqlDriverError:
        abort(503, "No database connection could be established.")


@app.teardown_request
def teardown_request(exception):
    try:
        g.rdb_conn.close()
    except AttributeError:
        pass


@app.route('/')
def index():
    return render_template('upload.html')


@app.route('/upload-fonts', methods=["POST"])
def upload_fonts():
    """Upload/download the two sets of fonts to compare"""

    # # Is the upload using Ajax, or a direct POST by the form?
    form = request.form
    is_ajax = True if form.get("__ajax", None) == "true" else False

    uuid = str(uuid4())

    # User wants to compare fonts against GF hosted.
    if form.get('fonts') == 'from_gf':
        after = downloadfonts.user_upload(request, "fonts_after")
        fonts_after = models.add_fonts(after, 'after', uuid)

        families_to_dl = set(map(filename_to_family_name, after))
        before = downloadfonts.google_fonts(families_to_dl)
        fonts_before = models.add_fonts(before, 'before', uuid)

    # User wants to compare upstream github fonts against GF hosted.
    elif form.get('fonts') == 'from_github_url':
        after = downloadfonts.github_dir(form.get('github-url'))
        fonts_after = models.add_fonts(after, 'after', uuid)

        before = downloadfonts.google_fonts(map(os.path.basename, after))
        fonts_before = models.add_fonts(before, 'before', uuid)

    # User wants to compare two sets of local fonts.
    elif form.get('fonts') == 'from_local':
        after = downloadfonts.user_upload(request, "fonts_after")
        fonts_after = models.add_fonts(after, 'after', uuid)

        before = downloadfonts.user_upload(request, "fonts_before")
        fonts_before = models.add_fonts(before, 'before', uuid)

    fontset = models.add_fontset(fonts_before, fonts_after, uuid)
    r.table('fontsets').insert(fontset).run(g.rdb_conn)

    font_diffs = models.add_font_diffs(fonts_before, fonts_after, uuid)
    r.table('font_diffs').insert(font_diffs).run(g.rdb_conn)

    if is_ajax:
        return ajax_response(True, uuid)
    return redirect(url_for("compare", uid=uuid, view="glyphs_new"))


def ajax_response(status, msg):
    status_code = "ok" if status else "error"
    return json.dumps(dict(
        status=status_code,
        msg=msg,
    ))


@app.route('/compare/<uuid>', defaults={"view": "glyphs_new", "font_size": 60})
@app.route('/compare/<uuid>/<view>', defaults={"font_size": 60})
@app.route('/compare/<uuid>/<view>/<font_size>')
def compare(uuid, view, font_size):
    fonts = list(r.table('fontsets')
                .filter({'uuid': uuid}).run(g.rdb_conn))[0]
    font_diffs = list(r.table('font_diffs')
                .filter({'uuid': uuid, 'view': view}).run(g.rdb_conn))

    if not font_diffs and view not in ['editor', 'waterfall']:
        return render_template('404.html'), 404

    return render_template(
        "test_fonts.html",
        fonts=fonts,
        font_diffs=font_diffs,
        font_position='before',
        limit=DIFF_LIMIT,
        view=view,
        views=VIEWS,
        uuid=uuid,
        font_size=int(font_size)
    )


@app.route('/screenshot/<uuid>/<view>/<font_position>',
           defaults={'font_size': 60})
@app.route('/screenshot/<uuid>/<view>/<font_position>/<font_size>')
def screenshot(uuid, view, font_position, font_size):
    """View gets used with Browserstack's screenshot api"""
    fonts = list(r.table('fontsets')
                .filter({'uuid': uuid}).run(g.rdb_conn))[0]
    font_diffs = list(r.table('font_diffs')
                .filter({'uuid': uuid, 'view': view}).run(g.rdb_conn))

    if not font_diffs and view not in ['editor', 'waterfall']:
        return render_template('404.html'), 404

    return render_template(
        "screenshot.html",
        fonts=fonts,
        font_diffs=font_diffs,
        view=view,
        font_position=font_position,
        font_size=int(font_size),
        limit=DIFF_LIMIT
    )


@app.route("/api/upload/<upload_type>", methods=['POST'])
def api_upload_fonts(upload_type):
    """Upload fonts via the api.
    TODO (M Foley) use std upload_fonts view"""
    uuid = str(uuid4())

    try:
        if upload_type == 'googlefonts':
            after = downloadfonts.user_upload(request, "fonts_after")
            fonts_after = models.add_fonts(after, 'after', uuid)

            families_to_dl = set(map(filename_to_family_name, after))
            before = downloadfonts.google_fonts(families_to_dl)
            fonts_before = models.add_fonts(before, 'before', uuid)

        elif upload_type == 'user':
            after = downloadfonts.user_upload(request, "fonts_after")
            fonts_after = models.add_fonts(after, 'after', uuid)

            before = downloadfonts.user_upload(request, "fonts_before")
            fonts_before = models.add_fonts(before, 'before', uuid)

        fontset = models.add_fontset(fonts_before, fonts_after, uuid)
        r.table('fontsets').insert(fontset).run(g.rdb_conn)

        font_diffs = models.add_font_diffs(fonts_before, fonts_after, uuid)
        r.table('font_diffs').insert(font_diffs).run(g.rdb_conn)

    except Exception, e:
        return json.dumps({'error': str(e)})
    return redirect(url_for("api_uuid_info", uuid=uuid))


@app.route("/api/info/<uuid>")
def api_uuid_info(uuid):
    """Return info regarding a uuid comparison"""
    fonts = list(r.table('fontsets')
                 .filter({'uuid': uuid}).run(g.rdb_conn))[0]
    return json.dumps({
        'uuid': uuid,
        'fonts': [f['full_name'] for f in fonts['after']['ttfs']]
    })


@app.errorhandler(500)
def internal_error(error):
    import traceback
    return render_template(
        "error.html",
        traceback=traceback.format_exc()
    )


@app.errorhandler(404)
def not_found(error):
    return render_template(
        "404.html"), 404


if __name__ == "__main__":
    app.run(debug=True)
