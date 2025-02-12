from __future__ import unicode_literals

import json
import re
import socket

from .common import InfoExtractor
from ..compat import (
    compat_etree_fromstring,
    compat_http_client,
    compat_urllib_error,
    compat_urllib_parse_unquote,
    compat_urllib_parse_unquote_plus,
)
from ..utils import (
    error_to_compat_str,
    ExtractorError,
    limit_length,
    sanitized_Request,
    urlencode_postdata,
    get_element_by_id,
    clean_html,
)


class FacebookIE(InfoExtractor):
    _VALID_URL = r'''(?x)
                (?:
                    https?://
                        (?:\w+\.)?facebook\.com/
                        (?:[^#]*?\#!/)?
                        (?:
                            (?:
                                video/video\.php|
                                photo\.php|
                                video\.php|
                                video/embed
                            )\?(?:.*?)(?:v|video_id)=|
                            [^/]+/videos/(?:[^/]+/)?
                        )|
                    facebook:
                )
                (?P<id>[0-9]+)
                '''
    _LOGIN_URL = 'https://www.facebook.com/login.php?next=http%3A%2F%2Ffacebook.com%2Fhome.php&login_attempt=1'
    _CHECKPOINT_URL = 'https://www.facebook.com/checkpoint/?next=http%3A%2F%2Ffacebook.com%2Fhome.php&_fb_noscript=1'
    _NETRC_MACHINE = 'facebook'
    IE_NAME = 'facebook'

    _CHROME_USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/48.0.2564.97 Safari/537.36'

    _TESTS = [{
        'url': 'https://www.facebook.com/video.php?v=637842556329505&fref=nf',
        'md5': '6a40d33c0eccbb1af76cf0485a052659',
        'info_dict': {
            'id': '637842556329505',
            'ext': 'mp4',
            'title': 're:Did you know Kei Nishikori is the first Asian man to ever reach a Grand Slam',
            'uploader': 'Tennis on Facebook',
        }
    }, {
        'note': 'Video without discernible title',
        'url': 'https://www.facebook.com/video.php?v=274175099429670',
        'info_dict': {
            'id': '274175099429670',
            'ext': 'mp4',
            'title': 'Facebook video #274175099429670',
            'uploader': 'Asif Nawab Butt',
        },
        'expected_warnings': [
            'title'
        ]
    }, {
        'note': 'Video with DASH manifest',
        'url': 'https://www.facebook.com/video.php?v=957955867617029',
        'md5': '54706e4db4f5ad58fbad82dde1f1213f',
        'info_dict': {
            'id': '957955867617029',
            'ext': 'mp4',
            'title': 'When you post epic content on instagram.com/433 8 million followers, this is ...',
            'uploader': 'Demy de Zeeuw',
        },
    }, {
        'url': 'https://www.facebook.com/video.php?v=10204634152394104',
        'only_matching': True,
    }, {
        'url': 'https://www.facebook.com/amogood/videos/1618742068337349/?fref=nf',
        'only_matching': True,
    }, {
        'url': 'https://www.facebook.com/ChristyClarkForBC/videos/vb.22819070941/10153870694020942/?type=2&theater',
        'only_matching': True,
    }, {
        'url': 'facebook:544765982287235',
        'only_matching': True,
    }]

    def _login(self):
        (useremail, password) = self._get_login_info()
        if useremail is None:
            return

        login_page_req = sanitized_Request(self._LOGIN_URL)
        self._set_cookie('facebook.com', 'locale', 'en_US')
        login_page = self._download_webpage(login_page_req, None,
                                            note='Downloading login page',
                                            errnote='Unable to download login page')
        lsd = self._search_regex(
            r'<input type="hidden" name="lsd" value="([^"]*)"',
            login_page, 'lsd')
        lgnrnd = self._search_regex(r'name="lgnrnd" value="([^"]*?)"', login_page, 'lgnrnd')

        login_form = {
            'email': useremail,
            'pass': password,
            'lsd': lsd,
            'lgnrnd': lgnrnd,
            'next': 'http://facebook.com/home.php',
            'default_persistent': '0',
            'legacy_return': '1',
            'timezone': '-60',
            'trynum': '1',
        }
        request = sanitized_Request(self._LOGIN_URL, urlencode_postdata(login_form))
        request.add_header('Content-Type', 'application/x-www-form-urlencoded')
        try:
            login_results = self._download_webpage(request, None,
                                                   note='Logging in', errnote='unable to fetch login page')
            if re.search(r'<form(.*)name="login"(.*)</form>', login_results) is not None:
                error = self._html_search_regex(
                    r'(?s)<div[^>]+class=(["\']).*?login_error_box.*?\1[^>]*><div[^>]*>.*?</div><div[^>]*>(?P<error>.+?)</div>',
                    login_results, 'login error', default=None, group='error')
                if error:
                    raise ExtractorError('Unable to login: %s' % error, expected=True)
                self._downloader.report_warning('unable to log in: bad username/password, or exceeded login rate limit (~3/min). Check credentials or wait.')
                return

            fb_dtsg = self._search_regex(
                r'name="fb_dtsg" value="(.+?)"', login_results, 'fb_dtsg', default=None)
            h = self._search_regex(
                r'name="h"\s+(?:\w+="[^"]+"\s+)*?value="([^"]+)"', login_results, 'h', default=None)

            if not fb_dtsg or not h:
                return

            check_form = {
                'fb_dtsg': fb_dtsg,
                'h': h,
                'name_action_selected': 'dont_save',
            }
            check_req = sanitized_Request(self._CHECKPOINT_URL, urlencode_postdata(check_form))
            check_req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            check_response = self._download_webpage(check_req, None,
                                                    note='Confirming login')
            if re.search(r'id="checkpointSubmitButton"', check_response) is not None:
                self._downloader.report_warning('Unable to confirm login, you have to login in your browser and authorize the login.')
        except (compat_urllib_error.URLError, compat_http_client.HTTPException, socket.error) as err:
            self._downloader.report_warning('unable to log in: %s' % error_to_compat_str(err))
            return

    def _real_initialize(self):
        self._login()

    def _real_extract(self, url):
        video_id = self._match_id(url)
        req = sanitized_Request('https://www.facebook.com/video/video.php?v=%s' % video_id)
        req.add_header('User-Agent', self._CHROME_USER_AGENT)
        webpage = self._download_webpage(req, video_id)

        video_data = None

        BEFORE = '{swf.addParam(param[0], param[1]);});\n'
        AFTER = '.forEach(function(variable) {swf.addVariable(variable[0], variable[1]);});'
        m = re.search(re.escape(BEFORE) + '(.*?)' + re.escape(AFTER), webpage)
        if m:
            data = dict(json.loads(m.group(1)))
            params_raw = compat_urllib_parse_unquote(data['params'])
            video_data = json.loads(params_raw)['video_data']

        def video_data_list2dict(video_data):
            ret = {}
            for item in video_data:
                format_id = item['stream_type']
                ret.setdefault(format_id, []).append(item)
            return ret

        if not video_data:
            server_js_data = self._parse_json(self._search_regex(
                r'handleServerJS\(({.+})\);', webpage, 'server js data'), video_id)
            for item in server_js_data['instances']:
                if item[1][0] == 'VideoConfig':
                    video_data = video_data_list2dict(item[2][0]['videoData'])
                    break

        if not video_data:
            m_msg = re.search(r'class="[^"]*uiInterstitialContent[^"]*"><div>(.*?)</div>', webpage)
            if m_msg is not None:
                raise ExtractorError(
                    'The video is not available, Facebook said: "%s"' % m_msg.group(1),
                    expected=True)
            else:
                raise ExtractorError('Cannot parse data')

        formats = []
        for format_id, f in video_data.items():
            if not f or not isinstance(f, list):
                continue
            for quality in ('sd', 'hd'):
                for src_type in ('src', 'src_no_ratelimit'):
                    src = f[0].get('%s_%s' % (quality, src_type))
                    if src:
                        formats.append({
                            'format_id': '%s_%s_%s' % (format_id, quality, src_type),
                            'url': src,
                            'preference': -10 if format_id == 'progressive' else 0,
                        })
            dash_manifest = f[0].get('dash_manifest')
            if dash_manifest:
                formats.extend(self._parse_mpd_formats(
                    compat_etree_fromstring(compat_urllib_parse_unquote_plus(dash_manifest))))
        if not formats:
            raise ExtractorError('Cannot find video formats')

        self._sort_formats(formats)

        video_title = self._html_search_regex(
            r'<h2\s+[^>]*class="uiHeaderTitle"[^>]*>([^<]*)</h2>', webpage, 'title',
            default=None)
        if not video_title:
            video_title = self._html_search_regex(
                r'(?s)<span class="fbPhotosPhotoCaption".*?id="fbPhotoPageCaption"><span class="hasCaption">(.*?)</span>',
                webpage, 'alternative title', default=None)
            video_title = limit_length(video_title, 80)
        if not video_title:
            video_title = 'Facebook video #%s' % video_id
        uploader = clean_html(get_element_by_id('fbPhotoPageAuthorName', webpage))

        return {
            'id': video_id,
            'title': video_title,
            'formats': formats,
            'uploader': uploader,
        }


class FacebookPostIE(InfoExtractor):
    IE_NAME = 'facebook:post'
    _VALID_URL = r'https?://(?:\w+\.)?facebook\.com/[^/]+/posts/(?P<id>\d+)'
    _TEST = {
        'url': 'https://www.facebook.com/maxlayn/posts/10153807558977570',
        'md5': '037b1fa7f3c2d02b7a0d7bc16031ecc6',
        'info_dict': {
            'id': '544765982287235',
            'ext': 'mp4',
            'title': '"What are you doing running in the snow?"',
            'uploader': 'FailArmy',
        }
    }

    def _real_extract(self, url):
        post_id = self._match_id(url)

        webpage = self._download_webpage(url, post_id)

        entries = [
            self.url_result('facebook:%s' % video_id, FacebookIE.ie_key())
            for video_id in self._parse_json(
                self._search_regex(
                    r'(["\'])video_ids\1\s*:\s*(?P<ids>\[.+?\])',
                    webpage, 'video ids', group='ids'),
                post_id)]

        return self.playlist_result(entries, post_id)
