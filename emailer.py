import envelopes
import envelopes.connstack
from flask import Flask
import flask
import hmac
import logging
import os
import sha

app = Flask(__name__)
conn = envelopes.SendGridSMTP(
    login=os.environ.get('SENDGRID_USERNAME'),
    password=os.environ.get('SENDGRID_PASSWORD')
)

logging.basicConfig(level=logging.INFO)

@app.before_request
def app_before_request():
    envelopes.connstack.push_connection(conn)


@app.after_request
def app_after_request(response):
    envelopes.connstack.pop_connection()
    return response


@app.route('/')
def index():
    """Redirect to chapel homepage."""
    return flask.redirect('http://chapel-lang.org/', code=301)


@app.route('/commit-email', methods=['POST'])
def commit_email():
    """Receive web hook from github and generate email."""

    # Only look at push events. Ignore the rest.
    event = flask.request.headers['x-github-event']
    logging.info('Received "{0}" event from github.'.format(event))
    if event != 'push':
        logging.info('Skipping "{0}" event.'.format(event))
        return 'nope'

    # Verify signature.
    secret = os.environ.get('CHAPEL_EMAILER_SECRET')
    if not secret:
        logging.error('No secret configured in environment.')
        raise ValueError('No secret configured in environment.')

    expected_signature = 'sha1=' + hmac.new(secret, flask.request.body, sha)
    gh_signature = flask.request.headers.get('x-hub-signature', '')
    if not hmac.compare_digest(expected_signature, gh_signature):
        logging.warning('Invalid signature, skipping request.')
        return 'nope'

    json_dict = flask.request.get_json()
    logging.info('json body: {0}'.format(json_dict))

    if json_dict['deleted']:
        logging.info('Branch was deleted, skipping email.')
        return 'nope'

    added = '\n'.join(map(lambda f: 'A {0}'.format(f),
                          json_dict['head_commit']['added']))
    removed = '\n'.join(map(lambda f: 'R {0}'.format(f),
                            json_dict['head_commit']['removed']))
    modified = '\n'.join(map(lambda f: 'M {0}'.format(f),
                             json_dict['head_commit']['modified']))
    changes = '\n'.join([added, removed, modified])

    msg_info = {
        'repo': json_dict['repository']['full_name'],
        'revision': json_dict['head_commit']['id'],
        'message': json_dict['head_commit']['message'],
        'changed_files': changes,
        'pusher': json_dict['pusher']['name'],
        'compare_url': json_dict['compare'],
    }

    sender = os.environ.get('CHAPEL_EMAILER_SENDER')
    recipient = os.environ.get('CHAPEL_EMAILER_RECIPIENT')

    subject_msg = msg_info['message'].splitlines()[0]
    subject_msg = subject_msg[:50]
    subject = '[{0}] {1}'.format(msg_info['repo'], subject_msg)

    body = """Revision: {revision}
Author: {pusher}

Log Message:
------------
{message}

Modified Files:
---------------
{changed_files}

Compare: {compare_url}
""".format(**msg_info)

    msg = envelopes.Envelope(
        to_addr=recipient,
        from_addr=sender,
        subject=subject,
        text_body=body
    )
    smtp = envelopes.connstack.get_current_connection()
    logging.info('Sending email: {0}'.format(msg))
    smtp.send(msg)

    return 'yep'
