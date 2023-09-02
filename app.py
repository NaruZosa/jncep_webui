"""JNCEP as a web application"""
import logging
import os
import shutil
import sys
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, send_file, render_template, request, abort, json
from jncep.cli.epub import generate_epub
from jncep.jncweb import BadWebURLError
from loguru import logger
from waitress import serve

app = Flask(__name__)


def terminate_request(message, status_code=200):
    """
    Terminates early, presenting the requestor with the provided message and response code
    :param message: Message to display to requestor
    :param status_code: HTTP return code
    """
    data = {"message": message}
    response = app.response_class(
        response=json.dumps(data),
        status=status_code,
        mimetype='application/json'
    )
    abort(response)


def create_epub(jnc_user, jnc_url, part_spec):
    """Create an epub file from a J-Novel Club URL and part specifier"""
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M_%f')
    output_dirpath = fr"{output_root}\{request.remote_addr}\{timestamp}"
    Path.mkdir(Path(output_dirpath), parents=True, exist_ok=True)
    logger.info(f"Generating epub(s) at {output_dirpath}")
    # Pass input to jncep per https://github.com/pallets/click/issues/40#issuecomment-326014129
    try:
        generate_epub.callback(jnc_url, jnc_user["email"], jnc_user["password"], part_spec,
                               output_dirpath, args["is_by_volume"], args["is_extract_images"],
                               args["is_extract_content"], args["is_not_replace_chars"],
                               args["style_css_path"])
    except BadWebURLError as exception:
        return terminate_request(exception)
    logger.info("Epub(s) generated")
    return output_dirpath


def make_one_bytesio_file(directory):
    """Reads directory, returns the file path and name if only one file.
    If multiple files, zips them into a BytesIO object and returns this with the series name"""
    # Path glob doesn't support len as it is a generator, so it needs to be turned into a list
    files = list(Path(directory).glob('*.epub'))
    if len(list(files)) == 1:
        # Single file, but read into a BytesIO object so the actual file can be deleted later
        with open(files[0], "rb") as file:
            memory_file = BytesIO(file.read())
        return memory_file, files[0].name
    # Multiple files. Zipping them together, so they can be sent
    logger.info(f"There are {len(list(files))} files. Zipping them together.")
    memory_file = BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in files:
            zipf.write(file, arcname=file.name)
    memory_file.seek(0)  # Return to start of file. 'Re-wind the tape'.
    # Get index of '_Volume_' and use this to get the filename up to read the series title.
    return memory_file, f"{files[0].stem[:files[0].stem.index('_Volume_')]}.zip"


def get_credentials(request_args=None):
    """Gets J-Novel Club user credentials.
    Uses environment variables if present, overriding with request args if provided"""
    logger.info("Reading J-Novel Club credentials")
    # os.getenv returns 'None' if not present
    jnc_user = {"email": os.getenv("JNCEP_EMAIL"), "password": os.getenv("JNCEP_PASSWORD")}
    if request_args is not None:
        if "JNCEP_EMAIL" in request_args:
            jnc_user["email"] = request_args["JNCEP_EMAIL"]
        if "JNCEP_PASSWORD" in request_args:
            jnc_user["password"] = request_args["JNCEP_PASSWORD"]
    if jnc_user["email"] is None or jnc_user["password"] is None:
        return terminate_request("J-Novel Club credentials missing", 401)
    return jnc_user


@app.route('/')
def homepage():
    """Load homepage"""
    logger.debug(f"Requesting IP: {request.remote_addr}")
    logger.debug("Requested homepage")
    # Read credentials, to terminate early if missing
    get_credentials()
    return render_template('Homepage.html')


@app.route('/epub', methods=['GET', 'POST'])
def index():
    """User requested an ebook, process request and return their ebook as a download"""
    logger.info(f"Prepub parts requested by client: {request.remote_addr}")
    # noinspection PyTypeChecker
    request_args = request.args.to_dict(flat=True)
    request_args['jnovelclub_url'] = unquote(request_args['jnovelclub_url'])
    logger.debug(f"Requested JNC URL: {request_args['jnovelclub_url']}")
    if "prepub_parts" in request_args and request_args["prepub_parts"] != "":
        logger.info(f"Requested parts: {request_args['prepub_parts']}")
    else:
        logger.info("Requested ALL")
        request_args['prepub_parts'] = ''

    # Get user credentials
    jnc_user = get_credentials(request_args)

    # Create the epub and store the save path
    epub_path = create_epub(jnc_user, request_args['jnovelclub_url'], request_args['prepub_parts'])

    # If multiple files, zip them up
    file_object, filename = make_one_bytesio_file(epub_path)

    # Delete the file directory, the file to be sent is a BytesIO object.
    logger.debug("Deleting epub(s)")
    shutil.rmtree(Path(epub_path).parent, ignore_errors=True)

    logger.info("Sending requested epub(s)")
    return send_file(file_object, download_name=filename, as_attachment=True)


class InterceptHandler(logging.Handler):
    """Intercept the stdlib logger"""

    def emit(self, record):
        """Intercept the stdlib logger"""
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


if __name__ == "__main__":
    logger.remove()
    logging.basicConfig(handlers=[InterceptHandler()], level=0)
    logger.add(sys.stderr, colorize=True,
               format="<level>{time:YYYY-MM-DD at HH:mm:ss} [{level}]</level> - {message}",
               level="DEBUG")
    logger.add("/logs/jncep.log", level="DEBUG",
               format="{time:YYYY-MM-DD at HH:mm:ss} | {level} | {message}",
               rotation="50 MB", compression="zip", retention="1 week")

    args = {"is_by_volume": True, "is_extract_images": False, "is_extract_content": False,
            "is_not_replace_chars": False, "style_css_path": False}

    if "JNCEP_OUTPUT" in os.environ:
        output_root = os.environ["JNCEP_OUTPUT"]
    else:
        output_root = "/output"

    serve(app, host="0.0.0.0", port=5000)
