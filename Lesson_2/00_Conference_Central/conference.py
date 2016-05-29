#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime
import json
import os
import time
import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import urlfetch
from google.appengine.ext import ndb
from google.appengine.api import memcache
from google.appengine.api import taskqueue

from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import TeeShirtSize
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import BooleanMessage
from models import ConflictException
from models import StringMessage
from models import Session
from models import SessionForm
from models import SessionForms
from models import Speaker
from models import SpeakerForm
from models import SpeakerQueryForm

from utils import getUserId

from settings import WEB_CLIENT_ID

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    session_type=messages.StringField(2),
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_WISHLIST_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1),
)


DEFAULTS = {
            "city": "Default City",
            "maxAttendees": 0,
            "seatsAvailable": 0,
            "topics": [ "Default", "Topic" ],
            }

SESSION_DEFAULTS = {
            "highlights": ["Default", "highlights"],
            "speaker": "Default speaker",
            "duration": 0,
            "type_of_session": "default type",
            "start_time": 0
            }

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api( name='conference',
                version='v1',
                allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
                scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):

# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        ## TODO 2
        ## step 1: make sure user is authed
        ## uncomment the following lines:
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        ## step 2: create a new Profile from logged in user data
        ## you can use user.nickname() to get displayName
        ## and user.email() to get mainEmail
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(), 
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', 
            http_method='GET', 
            name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    # TODO 1
    # 1. change request class
    # 2. pass request to _doProfile function
    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', 
            http_method='POST', 
            name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        # both for data model & outbound Message
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
            setattr(request, "seatsAvailable", data["maxAttendees"])

        # make Profile Key from user ID
        p_key = ndb.Key(Profile, user_id)
        # allocate new Conference ID with Profile key as parent
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        # make Conference key from ID
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )

        return request

    @endpoints.method(ConferenceForm, ConferenceForm, 
        path='conference', 
        http_method='POST', 
        name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
        path='queryConferences',
        http_method='POST',
        name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

         # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") \
            for conf in conferences]
        )

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
        path='getConferencesCreated', 
        http_method='POST', 
        name='getConferencesCreated')
    def getConferencesCreated(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        p_key = ndb.Key(Profile, getUserId(user))
        conferences = Conference.query(ancestor=p_key)

        prof = p_key.get()
        displayName = getattr(prof, 'displayName')

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, displayName) for conf in conferences]
        )

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
        path='filterPlayground',
        http_method='GET', 
        name='filterPlayground')
    def filterPlayground(self, request):
        q = Conference.query()
        q = q.filter(Conference.city == "London")
        q = q.filter(Conference.topics == "Medical Innovations")
        q = q.order(Conference.name)
        q = q.filter(Conference.maxAttendees > 10)
        

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', 
            name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))



# - - - Registration - - - - - - - - - - - - - - - - - - - -
    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', 
            name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', 
            name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        # step 1: get user profile
        user = self._getProfileFromUser()

        # step 2: get conferenceKeysToAttend from profile.
        # to make a ndb key from websafe key you can use:
        # ndb.Key(urlsafe=my_websafe_key_string)
        conf_keys = [ndb.Key(urlsafe=x) for x in user.conferenceKeysToAttend]

        # step 3: fetch conferences from datastore. 
        # Use get_multi(array_of_keys) to fetch all keys at once.
        # Do not fetch them one by one!
        conferences = ndb.get_multi(conf_keys)

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, "")\
         for conf in conferences]
        )

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
        path='conference/{websafeConferenceKey}/unregister',
        http_method='DELETE', 
        name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)

# - - - Announcements - - - - - - - - - - - - - - - - - - - -
    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', 
            name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)


# - - - Session objects - - - - - - - - - - - - - - - - -
    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm"""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                if field.name.endswith("date"):
                    setattr(sf, field.name, str(getattr(session, field.name)))
                else:
                    setattr(sf, field.name, getattr(session, field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, session.key.urlsafe())
        sf.check_initialized() 
        return sf

    @endpoints.method(message_types.VoidMessage, SessionForms,
        path='getAllSessions',
        http_method='GET',
        name='getAllSessions')
    def getAllSessions(self, request):
        """Returns all existing sessions"""
        sessions = Session.query()
        return SessionForms(items=[self._copySessionToForm(sess) for sess in sessions])

    #TODO 1
    # getConferenceSessions(websafeConferenceKey)
    @endpoints.method(CONF_GET_REQUEST, SessionForms,
        path='getConferenceSessions/{websafeConferenceKey}',
        http_method='POST',
        name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Given a conference websafekey, return all sessions for it"""
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        conf = conf_key.get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        sessions = Session.query(ancestor=conf_key)

        return SessionForms(items=[self._copySessionToForm(sess) for sess in sessions])
    
    #TODO 2
    # getConferenceSessionsByType(websafeConferenceKey, typeOfSession) 
    # (eg lecture, keynote, workshop)
    @endpoints.method(SESS_GET_REQUEST, SessionForms,
        path='getConferenceSessionsByType/{websafeConferenceKey}',
        http_method='GET',
        name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Given a conference, return all sessions of a specified type"""
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        sessions = Session.query(ancestor=c_key)
        sessions = sessions.filter(Session.type_of_session == request.session_type)

        return SessionForms(items=[self._copySessionToForm(sess) for sess in sessions])

    #TODO 3
    # getSessionsBySpeaker(speaker)
    @endpoints.method(SpeakerQueryForm, SessionForms,
        path='getSessionsBySpeaker',
        http_method='GET',
        name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Given a speaker, return all sessions given by this particular speaker
        across all conferences"""
        speaker = Speaker.query()
        speaker = speaker.filter(Speaker.name == request.name).get()
        if not speaker:
            raise endpoints.NotFoundException(
                'No speaker found with name %s' % request.name)
        sessions = speaker.hosting_sessions
        all_sessions = Session.query()
        speaker_sessions = []
        for session in sessions:
            speaker_sessions.append(all_sessions.filter(Session.name == session).get())

        return SessionForms(items=[self._copySessionToForm(sess) for sess in speaker_sessions])
    
    def _createSpeakerObject(self, request):
        """If a speaker has been found not to exist, this creates one
        and immediately adds the first session that references him or her"""
        speaker = Speaker(
            name = request.speaker,
            hosting_sessions = [request.name] # name of session
            )
        speaker.put()
        return None
        
    #TODO 4
    # createSession(SessionForm, websafeConferenceKey)
    # open only to the organizer of the conference
    def _createSessionObject(self, request):
        """Create Session object, returning SessionForm/request."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException("Authorization required")
        user_id = getUserId(user)

        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        conf = conf_key.get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException("Must be conference \
                organizer to add sessions to conference.")

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required.")

        data = {field.name: getattr(request, field.name)
            for field in request.all_fields()}
        del data['websafeConferenceKey']

        # fill in default values for missing fields
        for df in SESSION_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESSION_DEFAULTS[df]
                setattr(request, df, SESSION_DEFAULTS[df])

        # if a session date is provided, create a date object from the string
        if data["date"]:
            data["date"] = datetime.strptime(data["date"][:10], "%Y-%m-%d").date()
        else:
            data["date"] = conf.startDate

        # allocate new Session ID with Conference key as parent
        s_id = Session.allocate_ids(size=1, parent=conf_key)[0]
        # create a Session key from ID
        s_key = ndb.Key(Session, s_id, parent=conf_key)

        data['key'] = s_key

        # create Session and return modified SessionForm
        Session(**data).put()

        # TODO: Make email look nicer for user.
        taskqueue.add(params={'email': user.email(),
            'sessionInfo': repr(request)},
            url='/tasks/send_session_email'
        )
        
        if request.speaker:
            speaker = Speaker.query()
            speaker = speaker.filter(Speaker.name == request.speaker).get()
            if not speaker:
                self._createSpeakerObject(request)
            else:
                speaker.hosting_sessions.append(request.name)
                speaker.put()     
        
        return self._copySessionToForm(request)

    @endpoints.method(SESS_POST_REQUEST, SessionForm,
        path='createSession',
        http_method='POST',
        name='createSession')
    def createSession(self, request):
        """Create a new session."""
        return self._createSessionObject(request)

# - - - Session wishlist - - - - - - - - - - - - - - - - -

    @endpoints.method(SESS_WISHLIST_GET_REQUEST, BooleanMessage,
        path='session/{websafeSessionKey}',
        http_method='POST',
        name='addSessionToWishlist')
    def addSessionToWishlist(self, request, add_to_list=True):
        '''Add session to user's list of sessions they want to attend.'''
        prof = self._getProfileFromUser()
        return_value = None

        wssk = request.websafeSessionKey
        session = ndb.Key(urlsafe=wssk).get()
        if not session:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % wssk)

        if add_to_list:
            if wssk in prof.session_wishlist:
                raise ConflictException(
                    "This session is already on your wishlist")

            prof.session_wishlist.append(wssk)
            return_value = True
        else:
            if wssk in prof.session_wishlist:
                prof.session_wishlist.remove(wssk)
                return_value = True
            else:
                return_value = False
        prof.put()
        return BooleanMessage(data=return_value)



# registers API
api = endpoints.api_server([ConferenceApi])


# ahtzfmNvbmZlcmVuY2UtY2VudGVyLXByb2plY3RyMwsSB1Byb2ZpbGUiFGNvcmFsdmFuZGFAZ21haWwuY29tDAsSCkNvbmZlcmVuY2UYkb8FDA
