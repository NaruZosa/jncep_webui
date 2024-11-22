"""JNCEP as a Web Application.

This application logs into J-Novel Club and downloads specific volumes or series parts as EPUB files,
which are then sent to the client device.
"""
import logging
import os
import shutil
import signal
import sys
import time
import types
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

import requests
from flask import Flask, abort, jsonify, render_template, request, send_file
from httpx import HTTPStatusError
from jncep.cli.epub import generate_epub
from jncep.core import JNCEPSession, fetch_meta, resolve_series, to_part_spec
from jncep.jncweb import BadWebURLError, resource_from_url
from jncep.spec import analyze_part_specs
from loguru import logger
from waitress import serve

# Configurations
DEFAULT_ARGS = {
    "is_by_volume": True,
    "is_extract_images": False,
    "is_extract_content": False,
    "is_not_replace_chars": False,
    "style_css_path": False,
}

# Set output directory from environment variable or default to '/output'
OUTPUT_ROOT = Path(os.getenv("JNCEP_OUTPUT", "/output"))

# Initialize Flask application
app = Flask(__name__)


def terminate_request(message: str, status_code: int = 400) -> None:
    """Aborts request with a JSON response.

    Args:
    ----
        message (str): Message to display to the requestor.
        status_code (int): HTTP status code to return.

    """
    abort(jsonify({"message": message}), status_code)


async def fetch_volume_id(jnc_user: dict, jnc_url: str, part_spec: str) -> str:
    """Fetch volume ID from J-Novel Club based on URL and part spec.

    Args:
        jnc_user (dict): User credentials.
        jnc_url (str): J-Novel Club resource URL.
        part_spec (str): Part specification, if provided.

    Returns:
        str: The volume ID.

    """
    async with JNCEPSession(jnc_user["email"], jnc_user["password"]) as session:
        jnc_resource = resource_from_url(jnc_url)
        series_id = await resolve_series(session, jnc_resource)
        series_meta = await fetch_meta(session, series_id)

    # Analyze part specifications if provided, else generate default part specs
    if part_spec:
        part_spec_obj = analyze_part_specs(part_spec)
        part_spec_obj.normalize_and_verify(series_meta)
    else:
        part_spec_obj = await to_part_spec(series_meta, jnc_resource)
    return part_spec_obj.volume_id


def login_user(jnc_user: dict) -> str:
    """Log into J-Novel Club and return user ID.

    Args:
        jnc_user (dict): J-Novel Club user credentials.

    Returns:
        str: User ID.

    """
    response = requests.post("https://labs.j-novel.club/app/v2/auth/login?format=json",
                             headers={"Accept": "application/json", "content-type": "application/json"},
                             json={"email": jnc_user["email"], "password": jnc_user["password"]}, timeout=10)
    return response.json().get("id")


def purchase_book(user_id: str, volume_id: str) -> None:
    """Purchases a book on J-Novel Club.

    Args:
        user_id (str): The user ID.
        volume_id (str): The volume ID to purchase.

    """

    # Notable responses:
    #   204: Success
    #   401: Unauthorized
    #   410: Session token expired
    #   404: Volume not found
    #   501: Can't buy manga at this time
    #   402: Not enough coins to purchase
    #   409: Already own this volume
    #   500: Internal server error (reported to us)
    #   Other: Unknown server error

    response = requests.post(f"https://labs.j-novel.club/app/v2/me/coins/redeem/{volume_id}?format=json",
                             headers={"Authorization": f"Bearer {user_id}"}, timeout=10)
    if not response.ok:
        logger.warning(f"Error purchasing book: {response.status_code}")
    else:
        logger.info("Book purchased successfully.")


async def generate_epub_files(jnc_user: dict, jnc_url: str, part_spec: str, output_dir: Path) -> None:
    """Generate EPUB files from J-Novel Club URL and part spec.

    Args:
        jnc_user (dict): J-Novel Club user credentials.
        jnc_url (str): J-Novel Club resource URL.
        part_spec (str): Part specification, if provided.
        output_dir (Path): Directory to save the generated EPUB files.

    """
    # Pass input to jncep per https://github.com/pallets/click/issues/40#issuecomment-326014129
    try:
        generate_epub.callback(jnc_url, jnc_user["email"], jnc_user["password"], part_spec, output_dir, **DEFAULT_ARGS)
        logger.info("EPUB(s) generated successfully.")
    except BadWebURLError as exception:
        terminate_request(exception, 400)
    except HTTPStatusError as exception:
        if "Payment Required" in exception.args[0]:
            logger.info("Purchasing book due to missing permission after a delay.")
            await retry_purchase(jnc_user, jnc_url, part_spec, output_dir)


async def retry_purchase(jnc_user: dict, jnc_url: str, part_spec: str, output_dir: Path) -> None:
    """Retries purchasing and generating EPUBs after a failed attempt.

    Args:
        jnc_user (dict): J-Novel Club user credentials.
        jnc_url (str): J-Novel Club resource URL.
        part_spec (str): Part specification, if provided.
        output_dir (Path): Directory to save the generated EPUB files.

    """
    time.sleep(10)  # noqa: ASYNC251
    volume_id = await fetch_volume_id(jnc_user, jnc_url, part_spec)
    logger.info("Read volume ID. Waiting 10 seconds before continuing to avoid rate limiting.")
    time.sleep(10)  # noqa: ASYNC251
    user_id = login_user(jnc_user)
    await purchase_book(user_id, volume_id)
    await generate_epub_files(jnc_user, jnc_url, part_spec, output_dir)


def create_epub_directory() -> Path:
    """Create a timestamped directory for storing EPUB files, organized by client IP.

    Returns:
        Path: Path to the newly created directory.

    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M_%f")  # noqa: DTZ005
    dir_path = OUTPUT_ROOT / request.remote_addr / timestamp
    dir_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"EPUB generation directory created: {dir_path}")
    return dir_path


def make_zip(directory: Path) -> (BytesIO, str):
    """Create a ZIP file from all EPUB files in a directory.

    Args:
        directory (Path): Directory containing EPUB files.

    Returns:
        BytesIO: In-memory ZIP file containing the EPUBs.
        str: Filename for the ZIP archive.

    """
    files = list(directory.glob("*.epub"))
    memory_file = BytesIO()
    if len(files) == 1:
        with Path(files[0]).open("rb") as file:
            memory_file.write(file.read())
        memory_file.seek(0)
        return memory_file, files[0].name
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in files:
            zip_file.write(file, arcname=file.name)
    memory_file.seek(0)
    zip_filename = f"{files[0].stem.split('_Volume_')[0]}.zip"
    return memory_file, zip_filename


def get_credentials(request_args: dict | None = None) -> dict:
    """Retrieve J-Novel Club credentials from env vars or request parameters.

    Args:
        request_args (dict, optional): Dictionary of request arguments.

    Returns:
        dict: Dictionary containing user email and password.

    """
    logger.info("Retrieving J-Novel Club credentials")
    credentials = {
        "email": os.getenv("JNCEP_EMAIL"),
        "password": os.getenv("JNCEP_PASSWORD"),
    }
    if request_args:
        credentials.update({
            "email": request_args.get("JNCEP_EMAIL", credentials["email"]),
            "password": request_args.get("JNCEP_PASSWORD", credentials["password"]),
        })
    if not all(credentials.values()):
        terminate_request("Missing J-Novel Club credentials", 401)
    return credentials


# noinspection PyUnusedLocal
def terminate(sigterm: signal.SIGTERM, frame: types.FrameType) -> None:  # noqa: ARG001
    """Terminates the application gracefully (required for Docker).

    Args:
    ----
        sigterm (signal.Signal): The termination signal.
        frame (types.FrameType): The execution frame.

    """
    logger.info("Termination signal sent.")
    sys.exit(0)


# Application routes and entry point for the WSGI server
@app.route("/")
def homepage() -> render_template:
    """Handle requests for the homepage.

    Returns
    -------
        homepage(render_template): Renders the homepage.

    """
    logger.info(f"Homepage requested by {request.remote_addr}")
    return render_template("Homepage.html")


@app.route("/epub", methods=["GET", "POST"])
async def download_epub() -> send_file:
    """Handle EPUB download requests.

    Returns
    -------
        requested_file(send_file): The requested EPUB file (or files in a zip if multiple volumes).

    """
    logger.info(f"EPUB request initiated by {request.remote_addr}")
    # noinspection PyTypeChecker
    request_args = request.args.to_dict(flat=True)
    request_args["jnovelclub_url"] = unquote(request_args.get("jnovelclub_url", ""))
    logger.debug(f"Requested JNC URL: {request_args['jnovelclub_url']}")
    if "prepub_parts" in request_args and not request_args["prepub_parts"]:
        logger.info(f"Requested parts: {request_args['prepub_parts']}")
    else:
        logger.info("Requested ALL")
        request_args["prepub_parts"] = ""

    # Get user credentials
    jnc_user = get_credentials(request_args)
    output_dirpath = create_epub_directory()
    await generate_epub_files(jnc_user, request_args["jnovelclub_url"], request_args.get("prepub_parts", ""), output_dirpath)
    file_obj, filename = make_zip(output_dirpath)
    # Delete the file directory, the file to be sent is a BytesIO object and already loaded into RAM.
    logger.debug("Deleting epub(s)")
    shutil.rmtree(Path(output_dirpath).parent, ignore_errors=True)
    logger.info("Sending requested epub(s)")
    return send_file(file_obj, download_name=filename, as_attachment=True)


def setup_logging() -> None:
    """Configure logging using Loguru with file rotation and console output."""
    class InterceptHandler(logging.Handler):
        """Intercept the stdlib logger."""

        def emit(self, record: logging.LogRecord) -> None:  # noqa: PLR6301
            """Intercept the stdlib logger."""
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
    logger.remove()
    logging.basicConfig(handlers=[InterceptHandler()], level=0)
    logger.add(sys.stderr, colorize=True, format="<level>{time:YYYY-MM-DD HH:mm:ss} [{level}]</level> - {message}", level="DEBUG")
    logger.add("/logs/jncep.log", level="DEBUG", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}", rotation="50 MB", compression="zip", retention="1 week")


def main() -> None:
    """Register sigterm handling, set up logging and serve the app."""
    signal.signal(signal.SIGTERM, terminate)
    setup_logging()
    serve(app, host="0.0.0.0", port=5000)  # noqa: S104


if __name__ == "__main__":
    main()
