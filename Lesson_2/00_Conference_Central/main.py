#!/usr/bin/env python

"""
main.py -- Udacity conference server-side Python App Engine
    HTTP controller handlers for memcache & task queue access

$Id$

created by wesc on 2014 may 24

"""

import webapp2
from google.appengine.api import app_identity
from google.appengine.api import mail
from google.appengine.api import memcache

from conference import ConferenceApi

MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"


class SetAnnouncementHandler(webapp2.RequestHandler):
    def get(self):
        """Set Announcement in Memcache."""
        # use _cacheAnnouncement() to set announcement in Memcache
        ConferenceApi._cacheAnnouncement()


class SendConfirmationEmailHandler(webapp2.RequestHandler):
    def post(self):
        """Send email confirming Conference creation."""
        mail.send_mail(
            'noreply@%s.appspotmail.com' % (
                app_identity.get_application_id()),     # from
            self.request.get('email'),                  # to
            'You created a new Conference!',            # subj
            'Hi, you have created a following '         # body
            'conference:\r\n\r\n%s' % self.request.get(
                'conferenceInfo')
        )


class SendSessionEmailHandler(webapp2.RequestHandler):
    def post(self):
        """Send email confirming Session creation."""
        message = mail.EmailMessage(sender='noreply@%s.appspotmail.com' % 
            (app_identity.get_application_id()),
            to=self.request.get('email'),                  
            subject='You created a new session!',   
            body='Hi, you have created the session:\r\n\r\n%s' %
            self.request.get('sessionInfo'))
        message.send()


class SetFeaturedSpeakerHandler(webapp2.RequestHandler):
    def post(self):
        """Set featured speaker in memcache"""
        speaker = self.request.get('speaker')
        #ConferenceApi._cacheFeaturedSpeaker(speaker)
        if speaker:
            memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, speaker)
        else:
            memcache.delete(MEMCACHE_FEATURED_SPEAKER_KEY)


app = webapp2.WSGIApplication([
    ('/crons/set_announcement', SetAnnouncementHandler),
    ('/tasks/send_confirmation_email', SendConfirmationEmailHandler),
    ('/tasks/send_session_email', SendSessionEmailHandler),
    ('/tasks/set_featured_speaker', SetFeaturedSpeakerHandler)
], debug=True)