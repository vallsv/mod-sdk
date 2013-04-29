#!/usr/bin/env python

import os, json, random, subprocess, re, base64
import Image

from tornado import web, options, ioloop, template, httpclient, escape
from modcommon import lv2

PORT = 9000
ROOT = os.path.dirname(os.path.realpath(__file__))
HTML_DIR = os.path.join(ROOT, 'html')
WORKSPACE = os.path.join(ROOT, 'workspace')
UNITS_FILE = os.path.join(ROOT, 'units.ttl')
CONFIG_FILE = os.path.join(ROOT, 'config.json')
DEFAULT_TEMPLATE = os.path.join(ROOT, 'html/resources/templates/default.html')
PHANTOM_BINARY = os.path.join(ROOT, 'phantomjs-1.9.0-linux-x86_64/bin/phantomjs')
SCREENSHOT_SCRIPT = os.path.join(ROOT, 'screenshot.js')
MAX_THUMB_WIDTH = 64
MAX_THUMB_HEIGHT = 64

def get_config(key, default=None):
    try:
        config = json.loads(open(CONFIG_FILE).read())
        return config[key]
    except:
        return default

class BundleList(web.RequestHandler):
    def get(self):
        bundles = []
        for bundle in os.listdir(WORKSPACE):
            if os.path.exists(os.path.join(WORKSPACE, bundle, 'manifest.ttl')):
                bundles.append(bundle)
        self.set_header('Content-type', 'application/json')
        self.write(json.dumps(bundles))

class EffectList(web.RequestHandler):
    def get(self, bundle):
        path = os.path.join(WORKSPACE, bundle)
        if not os.path.exists(os.path.join(path, 'manifest.ttl')):
            raise web.HTTPError(404)
        package = lv2.Bundle(path, units_file=UNITS_FILE)
        self.set_header('Content-type', 'application/json')
        self.write(package.data)
        
        
class Index(web.RequestHandler):
    def get(self, path):
        if not path:
            path = 'index.html'
        loader = template.Loader(HTML_DIR)
        default_template = open(DEFAULT_TEMPLATE).read()
        context = {
            'default_template': escape.squeeze(default_template.replace("'", "\\'")),
            }
        self.write(loader.load(path).generate(**context))

class Screenshot(web.RequestHandler):
    @web.asynchronous
    def get(self):
        self.bundle = self.get_argument('bundle')
        self.effect = self.get_argument('effect')
        self.width = self.get_argument('width')
        self.height = self.get_argument('height')

        self.make_screenshot()

    def tmp_filename(self):
        tmp_filename = ''.join([ random.choice('0123456789abcdef') for i in range(6) ])
        return '/tmp/%s.png' % tmp_filename

    def make_screenshot(self):
        fname = self.tmp_filename()
        proc = subprocess.Popen([ PHANTOM_BINARY, 
                                  SCREENSHOT_SCRIPT,
                                  'http://localhost:%d/icon.html#%s,%s' % (PORT, self.bundle, self.effect),
                                  fname,
                                  self.width,
                                  self.height,
                                  ],
                                stdout=subprocess.PIPE)

        def proc_callback(fileno, event):
            if proc.poll() is None:
                return
            loop.remove_handler(fileno)
            fh = open(fname)
            os.remove(fname)
            self.handle_image(fh)

        loop = ioloop.IOLoop.instance()
        loop.add_handler(proc.stdout.fileno(), proc_callback, 16)

    def handle_image(self, fh):
        icon_data = fh.read()
        fh.seek(0)
        thumb_data = self.thumbnail(fh).read()

        self.save_icon(icon_data, thumb_data)

        result = {
            'ok': True,
            'icon': base64.b64encode(icon_data),
            'thumbnail': base64.b64encode(thumb_data),
            }

        self.set_header('Content-type', 'application/json')
        self.write(json.dumps(result))
        self.finish()

    def thumbnail(self, fh):
        img = Image.open(fh)
        width, height = img.size
        if width > MAX_THUMB_WIDTH:
            width = MAX_THUMB_WIDTH
            height = height * MAX_THUMB_WIDTH / width
        if height > MAX_THUMB_HEIGHT:
            height = MAX_THUMB_HEIGHT
            width = width * MAX_THUMB_HEIGHT / height
        img.thumbnail((width, height))
        fname = self.tmp_filename()
        img.save(fname)
        fh = open(fname)
        os.remove(fname)
        return fh

    def save_icon(self, icon_data, thumb_data):
        path = os.path.join(WORKSPACE, self.bundle)
        package = lv2.Bundle(path, units_file=UNITS_FILE)
        effect = package.data['plugins'][self.effect]
        slug = effect['name'].lower()
        slug = re.sub('\s+', '-', slug)
        slug = re.sub('[^a-z0-9-]', '', slug)

        try:
            basedir = effect['icon']['basedir']
        except:
            basedir = os.path.join(path, 'modgui')
        if not os.path.exists(basedir):
            os.mkdir(basedir)

        icon_path = os.path.join(basedir, '%s-%s.png' % ('icon', slug))
        thumb_path = os.path.join(basedir, '%s-%s.png' % ('thumb', slug))

        open(icon_path, 'w').write(icon_data)
        open(thumb_path, 'w').write(thumb_data)

class BundleInstall(web.RequestHandler):
    @web.asynchronous
    def get(self, bundle):
        path = os.path.join(WORKSPACE, bundle)
        package = lv2.BundlePackage(path, units_file=UNITS_FILE)
        content_type, body = self.encode_multipart_formdata(package)

        headers = {
            'Content-Type': content_type,
            'Content-Length': str(len(body)),
            }

        client = httpclient.AsyncHTTPClient()
        addr = get_config('device', 'http://localhost:8888')
        if not addr.startswith('http://') and not addr.startswith('https://'):
            addr = 'http://%s' % addr
        if addr.endswith('/'):
            addr = addr[:-1]
        client.fetch('%s/sdk/install' % addr,
                     self.handle_response,
                     method='POST', headers=headers, body=body)

    def handle_response(self, response):
        self.set_header('Content-type', 'application/json')
        if (response.code == 200):
            self.write(json.dumps({ 'ok': json.loads(response.body) }))
        else:
            self.write(json.dumps({ 'ok': False,
                                    'error': response.body,
                                    }))
        self.finish()
        
    def encode_multipart_formdata(self, package):
        boundary = '----------%s' % ''.join([ random.choice('0123456789abcdef') for i in range(22) ])
        body = []

        body.append('--%s' % boundary)
        body.append('Content-Disposition: form-data; name="package"; filename="%s.tgz"' % package.uid)
        body.append('Content-Type: application/octet-stream')
        body.append('')
        body.append(package.read())
        
        body.append('--%s--' % boundary)
        body.append('')

        content_type = 'multipart/form-data; boundary=%s' % boundary

        return content_type, '\r\n'.join(body)

class ConfigurationGet(web.RequestHandler):
    def get(self):
        try:
            config = json.loads(open(CONFIG_FILE).read())
        except:
            config = {}
        self.set_header('Content-type', 'application/json')
        self.write(json.dumps(config))

class ConfigurationSet(web.RequestHandler):
    def post(self):
        config = json.loads(self.request.body)
        open(CONFIG_FILE, 'w').write(json.dumps(config))
        self.set_header('Content-type', 'application/json')
        self.write(json.dumps(True))

def run():
    application = web.Application([
            (r"/bundles", BundleList),
            (r"/effects/(.+)", EffectList),
            (r"/config/get", ConfigurationGet),
            (r"/config/set", ConfigurationSet),
            (r"/(icon.html)?", Index),
            (r"/screenshot", Screenshot),
            (r"/install/(.+)/?", BundleInstall),
            (r"/(.*)", web.StaticFileHandler, {"path": HTML_DIR}),
            ],
                                  debug=True)
    
    application.listen(PORT)
    options.parse_command_line()
    ioloop.IOLoop.instance().start()

if __name__ == "__main__":
    run()
