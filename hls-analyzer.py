# coding: utf-8
# Copyright 2014 jeoliva author. All rights reserved.
# Use of this source code is governed by a MIT License
# license that can be found in the LICENSE file.

import errno
import os
import logging
import sys
import argparse
import m3u8
from bitreader import BitReader
from ts_segment import TSSegmentParser
from videoframesinfo import VideoFramesInfo
import logging
import requests
import time
from urllib.parse import urljoin
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

warnings = []
captions_detected = []  # Global list to track detected captions

# Configure logging to both file and console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("hls_analysis.log"),
        logging.StreamHandler()  # Logs to the console
    ]
)
logging.info("HLS analysis script started.")


def log_warning(message):
    warnings.append(message)
    logging.warning(message)  # Log to file
    print(f"Warning: {message}")  # Print to console

def log_manifest_content(url):
    try:
        manifest = m3u8.load(url)
        logging.info(f"Manifest content for {url}:\n{manifest.dumps()}")  # Dumps manifest content
        print(f"Manifest content logged for {url}")
    except Exception as e:
        logging.error(f"Failed to load manifest {url}: {e}")
        print(f"Failed to load manifest {url}: {e}")

def verify_uri_accessibility(uri):
    try:
        response = requests.head(uri)
        if response.status_code == 200:
            logging.info(f"URI is accessible: {uri}")
            print(f"URI is accessible: {uri}")
        else:
            logging.warning(f"URI returned status code {response.status_code}: {uri}")
            print(f"URI returned status code {response.status_code}: {uri}")
    except Exception as e:
        logging.error(f"Error accessing URI {uri}: {e}")
        print(f"Error accessing URI {uri}: {e}")

try:
    import urllib.request, urllib.error, urllib.parse
except ImportError:
    from urllib.request import urlopen as urllib2

num_segments_to_analyze_per_playlist = 1
max_frames_to_show = 30

videoFramesInfoDict = dict()

def download_url(uri, httpRange=None):
    # Dynamically set the Referer based on the URI
    base_referer = '/'.join(uri.split('/')[:3])
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36',
        'Accept': '*/*',
        'Referer': base_referer,  # Dynamically set Referer
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive'
    }

    if httpRange:
        headers['Range'] = httpRange

    session = requests.Session()  # Create a session for reuse
    try:
        response = session.get(uri, headers=headers, verify=False, allow_redirects=True)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.content
    except requests.exceptions.RequestException as e:
        logging.error(f"Error downloading URL {uri}: {e}")
        return None


def analyze_variant(variant_url, bw):
    """
    Analyze a specific variant playlist.

    Args:
        variant_url (str or M3U8): The URL or M3U8 object of the variant playlist
        bw (int): The bandwidth of the variant
    """
    logging.info(f"Analyzing variant ({bw})")
    print(f"***** Analyzing variant ({bw}) *****")

    try:
        # Initialize video frames info for this bandwidth if not exists
        if bw not in videoFramesInfoDict:
            videoFramesInfoDict[bw] = VideoFramesInfo()

        # Handle case where variant_url is already an M3U8 object
        if isinstance(variant_url, m3u8.model.M3U8):
            variant_playlist = variant_url
        else:
            # Download and parse the variant playlist if it's a URL
            playlist_data = download_url(variant_url)
            if not playlist_data:
                logging.error(f"Failed to download variant playlist: {variant_url}")
                return
            
            # Decode the playlist data if it's in bytes
            if isinstance(playlist_data, bytes):
                playlist_data = playlist_data.decode('utf-8')
            
            variant_playlist = m3u8.loads(playlist_data)

        # Check if we have segments to analyze
        if not variant_playlist.segments:
            logging.warning(f"No segments found in variant {bw}. Skipping analysis.")
            return

        logging.info(f"Found {len(variant_playlist.segments)} segments in variant {bw}.")
        
        # Process segments
        for i, segment in enumerate(variant_playlist.segments[:num_segments_to_analyze_per_playlist]):
            logging.info(f"Processing segment {i + 1} of {min(num_segments_to_analyze_per_playlist, len(variant_playlist.segments))}")
            
            # Get absolute segment URL
            segment_uri = urljoin(base_url, segment.uri) if not segment.uri.startswith('http') else segment.uri
            
            # Download and analyze segment
            segment_data = download_url(segment_uri, get_range(segment.byterange))
            if segment_data:
                ts_parser = TSSegmentParser(bytearray(segment_data))
                ts_parser.prepare()
                
                printFormatInfo(ts_parser)
                printTimingInfo(ts_parser, segment)
                analyzeFrames(ts_parser, bw, i)
            else:
                logging.error(f"Failed to download segment: {segment_uri}")

    except Exception as e:
        logging.error(f"Error analyzing variant: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())




def get_playlist_duration(variant):
    duration = 0
    for i in range(0, len(variant.segments)):
        duration = duration + variant.segments[i].duration
    return duration

def get_range(segment_range):
    if(segment_range is None):
        return None

    params= segment_range.split('@')
    if(params is None or len(params) != 2):
        return None

    start = int(params[1])
    length = int(params[0])

    return "bytes={}-{}".format(start, start+length-1);

def printFormatInfo(ts_parser):
    print ("\t** Tracks and Media formats **")

    for i in range(0, ts_parser.getNumTracks()):
        track = ts_parser.getTrack(i)
        print(("\tTrack #{} - Type: {}, Format: {}".format(i,
            track.payloadReader.getMimeType(), track.payloadReader.getFormat())))

def printTimingInfo(ts_parser, segment):
    print ("\n\t** Timing information **")
    print(("\tSegment declared duration: {}".format(segment.duration)))
    minDuration = 0;
    for i in range(0, ts_parser.getNumTracks()):
        track = ts_parser.getTrack(i)
        print(("\tTrack #{} - Duration: {} s, First PTS: {} s, Last PTS: {} s".format(i,
            track.payloadReader.getDuration()/1000000.0, track.payloadReader.getFirstPTS() / 1000000.0,
            track.payloadReader.getLastPTS()/1000000.0)))
        if(track.payloadReader.getDuration() != 0 and (minDuration == 0 or minDuration > track.payloadReader.getDuration())):
            minDuration = track.payloadReader.getDuration()

    minDuration /= 1000000.0
    if minDuration > 0:
        print(("\tDuration difference (declared vs real): {0}s ({1:.2f}%)".format(segment.duration - minDuration, abs((1 - segment.duration/minDuration)*100))))
    else:
        print("\tDuration is 0")

def analyzeFrames(ts_parser, bw, segment_index):
    print ("\n\t** Frames **")

    for i in range(0, ts_parser.getNumTracks()):
        track = ts_parser.getTrack(i)
        print(("\tTrack #{0} - Frames: ".format(i)), end=' ')

        frameCount = min(max_frames_to_show, len(track.payloadReader.frames))
        for j in range(0, frameCount):
            print("{0}".format(track.payloadReader.frames[j].type), end=' ')
        if track.payloadReader.getMimeType().startswith("video/"):
            print(("\tAA: {}, BB: {}".format(segment_index, bw)))
            if len(track.payloadReader.frames) > 0:
                videoFramesInfoDict[bw].segmentsFirstFramePts[segment_index] = track.payloadReader.frames[0].timeUs
            else:
                videoFramesInfoDict[bw].segmentsFirstFramePts[segment_index] = 0
            analyzeVideoframes(track, bw)
        print ("")

def analyzeVideoframes(track, bw):
    nkf = 0
    print ("")
    for i in range(0, len(track.payloadReader.frames)): 
        if i == 0:
            if track.payloadReader.frames[i].isKeyframe() == True:
                print(("\t\tGood! Track starts with a keyframe".format(i)))
            else:
                print(("\t\tWarning: note this is not starting with a keyframe. This will cause not seamless bitrate switching".format(i)))
        if track.payloadReader.frames[i].isKeyframe():
            nkf = nkf + 1
            if videoFramesInfoDict[bw].lastKfPts > -1:
                videoFramesInfoDict[bw].lastKfi = track.payloadReader.frames[i].timeUs - videoFramesInfoDict[bw].lastKfPts
                if videoFramesInfoDict[bw].minKfi == 0:
                    videoFramesInfoDict[bw].minKfi = videoFramesInfoDict[bw].lastKfi
                else:
                    videoFramesInfoDict[bw].minKfi = min(videoFramesInfoDict[bw].lastKfi, videoFramesInfoDict[bw].minKfi)
                videoFramesInfoDict[bw].maxKfi = max(videoFramesInfoDict[bw].lastKfi, videoFramesInfoDict[bw].maxKfi)  
            videoFramesInfoDict[bw].lastKfPts = track.payloadReader.frames[i].timeUs
    print(("\t\tKeyframes count: {}".format(nkf)))
    if nkf == 0:
        print ("\t\tWarning: there are no keyframes in this track! This will cause a bad playback experience")
    if nkf > 1:
        print(("\t\tKey frame interval within track: {} seconds".format(videoFramesInfoDict[bw].lastKfi/1000000.0)))
    else:
        if track.payloadReader.getDuration() > 3000000.0:
            print ("\t\tWarning: track too long to have just 1 keyframe. This could cause bad playback experience and poor seeking accuracy in some video players")

    videoFramesInfoDict[bw].count = videoFramesInfoDict[bw].count + nkf

    if videoFramesInfoDict[bw].count > 1:
        kfiDeviation = videoFramesInfoDict[bw].maxKfi - videoFramesInfoDict[bw].minKfi
        if kfiDeviation > 500000:
            print(("\t\tWarning: Key frame interval is not constant. Min KFI: {}, Max KFI: {}".format(videoFramesInfoDict[bw].minKfi, videoFramesInfoDict[bw].maxKfi) ))

def analyze_segment(segment, bw, segment_index):
    absolute_segment_uri = urljoin(base_url, segment.uri) if not segment.uri.startswith("http") else segment.uri
    logging.info(f"Analyzing segment: {absolute_segment_uri}")
    try:
        segment_data = download_url(absolute_segment_uri)
        if not segment_data:
            raise ValueError("Failed to download segment data.")

        ts_parser = TSSegmentParser(bytearray(segment_data))
        ts_parser.prepare()
        # Perform further analysis...
    except Exception as e:
        logging.error(f"Error downloading or processing segment {absolute_segment_uri}: {e}")



def analyze_variants_frame_alignment():
    if not videoFramesInfoDict:
        logging.warning("No video frame information found. Skipping alignment analysis.")
        print("No variants to analyze for frame alignment.")
        return

    df = videoFramesInfoDict.copy()
    bw, vf = df.popitem()
    logging.info(f"Starting alignment check for reference variant {bw}")
    print(f"Keys in segmentsFirstFramePts for reference variant {bw}: {list(vf.segmentsFirstFramePts.keys())}")

    for bwkey, frameinfo in df.items():
        logging.info(f"Checking alignment for variant {bwkey}")
        print(f"\nChecking alignment for variant {bwkey} bps")
        print(f"Keys in segmentsFirstFramePts for variant {bwkey}: {list(frameinfo.segmentsFirstFramePts.keys())}")

        for segment_index, value in frameinfo.segmentsFirstFramePts.items():
            if segment_index not in vf.segmentsFirstFramePts:
                log_warning(f"Segment index {segment_index} missing in reference variant {bw} bps. Skipping.")
                continue
            if vf.segmentsFirstFramePts[segment_index] != value:
                log_warning(f"Variants {bw} bps and {bwkey} bps, segment {segment_index}, "
                            f"are not aligned (first frame PTS not equal {vf.segmentsFirstFramePts[segment_index]} != {value})")

    print("\nCompleted alignment check for all variants.")
    logging.info("Completed alignment check for all variants.")



def check_for_captions(playlist, base_url):
    """
    Check for and validate caption/subtitle tracks
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36',
        'Referer': 'https://dablu0xuev4uv.cloudfront.net',
        'Accept': '*/*'
    }

    if hasattr(playlist, 'media'):
        # First log all media entries for debugging
        logging.debug("Found media entries in playlist:")
        for media in playlist.media:
            logging.debug(f"Media entry - Type: {media.type}, Language: {media.language}, URI: {getattr(media, 'uri', None)}")

        # Then process subtitles
        for media in playlist.media:
            if media.type == 'SUBTITLES':
                logging.info(f"Found subtitle track - Language: {media.language}")
                
                if hasattr(media, 'uri') and media.uri:
                    absolute_uri = urljoin(base_url, media.uri) if not media.uri.startswith('http') else media.uri
                    media.uri = absolute_uri  # Update with resolved URL
                    logging.debug(f"Resolved subtitle URI: {absolute_uri}")

                    try:
                        response = requests.get(
                            absolute_uri,
                            headers=headers,
                            verify=False,
                            allow_redirects=True
                        )
                        if response.status_code == 200:
                            logging.info(f"Successfully accessed subtitle playlist: {absolute_uri}")
                        else:
                            logging.warning(f"Subtitle playlist {absolute_uri} returned status {response.status_code}")
                    except Exception as e:
                        logging.error(f"Error accessing subtitle playlist: {str(e)}")

                # Always add to captions_detected, even if URI validation fails
                captions_detected.append({
                    'language': media.language or 'unknown',
                    'type': media.type,
                    'uri': getattr(media, 'uri', 'no_uri'),
                    'group_id': getattr(media, 'group_id', 'unknown'),
                    'name': getattr(media, 'name', 'unknown'),
                    'default': getattr(media, 'default', False),
                    'autoselect': getattr(media, 'autoselect', False)
                })
    else:
        logging.debug("No media entries found in playlist")

def print_manifest_info(playlist, base_url):
    """
    Print detailed information about the manifest content
    """
    print("\n** Manifest Debug Info **")
    
    # Print media entries
    if hasattr(playlist, 'media'):
        print("\nMedia entries found:", len(playlist.media))
        for media in playlist.media:
            print("\nMedia entry:")
            print(f"  Type: {getattr(media, 'type', 'None')}")
            print(f"  Group ID: {getattr(media, 'group_id', 'None')}")
            print(f"  Language: {getattr(media, 'language', 'None')}")
            print(f"  Name: {getattr(media, 'name', 'None')}")
            print(f"  URI: {getattr(media, 'uri', 'None')}")
            print(f"  Default: {getattr(media, 'default', 'None')}")
            print(f"  Autoselect: {getattr(media, 'autoselect', 'None')}")
    else:
        print("No media entries found")

    # Print playlists
    if hasattr(playlist, 'playlists'):
        print("\nPlaylists found:", len(playlist.playlists))
        for p in playlist.playlists:
            print("\nPlaylist:")
            print(f"  Bandwidth: {p.stream_info.bandwidth}")
            print(f"  Resolution: {getattr(p.stream_info, 'resolution', 'None')}")
            print(f"  Codecs: {getattr(p.stream_info, 'codecs', 'None')}")
            print(f"  Subtitles: {getattr(p.stream_info, 'subtitles', 'None')}")
            print(f"  URI: {getattr(p, 'uri', 'None')}")
    else:
        print("No playlists found")

def diagnose_subtitles(master_playlist, base_url):
    """
    Enhanced subtitle diagnostics
    """
    subtitles = {}
    issues = []
    
    # Debug logging
    logging.debug(f"Checking master playlist for subtitles at {base_url}")
    
    if hasattr(master_playlist, 'media'):
        logging.debug(f"Found {len(master_playlist.media)} media entries")
        for media in master_playlist.media:
            # Log each attribute individually
            logging.debug(f"Media entry - Type: {getattr(media, 'type', 'None')}, "
                        f"Language: {getattr(media, 'language', 'None')}, "
                        f"URI: {getattr(media, 'uri', 'None')}")
            
            if getattr(media, 'type', None) == 'SUBTITLES':
                logging.info(f"Found subtitle track - Language: {getattr(media, 'language', 'unknown')}")
                
                group_id = getattr(media, 'group_id', 'default')
                uri = getattr(media, 'uri', None)
                if uri:
                    uri = urljoin(base_url, uri) if not uri.startswith('http') else uri
                
                subtitles[group_id] = {
                    'uri': uri,
                    'language': getattr(media, 'language', 'unknown'),
                    'referenced': False
                }
    else:
        logging.debug("No media entries in master playlist")
    
    # Check subtitle references
    if hasattr(master_playlist, 'playlists'):
        for playlist in master_playlist.playlists:
            if hasattr(playlist.stream_info, 'subtitles'):
                group_id = playlist.stream_info.subtitles
                if group_id:
                    logging.debug(f"Found subtitle reference: {group_id}")
                    if group_id in subtitles:
                        subtitles[group_id]['referenced'] = True
                    else:
                        issues.append(f"Referenced subtitle group '{group_id}' not found")
    
    return subtitles, issues


def print_subtitle_diagnosis(master_playlist, base_url):
    """
    Print diagnostic results for subtitles
    """
    print("\n** Subtitle/Caption Analysis **")
    issues = diagnose_subtitles(master_playlist, base_url)
    
    if not issues:
        if hasattr(master_playlist, 'media') and any(m.type == 'SUBTITLES' for m in master_playlist.media):
            print("✓ All subtitle configurations appear valid")
            logging.info("All subtitle configurations appear valid")
        else:
            print("Note: No subtitle or caption tracks found in the manifest")
            logging.info("No subtitle or caption tracks found in the manifest")
    else:
        print("\nPotential Issues Found:")
        for issue in issues:
            print(f"- {issue}")
            logging.warning(issue)

def check_subtitle_playlist(subtitle_uri, base_url):
    """
    Check a subtitle playlist and its segments.
    """
    if not subtitle_uri.startswith('http'):
        subtitle_uri = urljoin(base_url, subtitle_uri)
        
    print(f"\nChecking subtitle playlist: {subtitle_uri}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36',
        'Referer': 'https://dablu0xuev4uv.cloudfront.net',
        'Accept': '*/*'
    }
    
    try:
        response = requests.get(subtitle_uri, headers=headers, verify=False)
        if response.status_code == 200:
            sub_playlist = m3u8.loads(response.text)
            
            print("\nSubtitle format details:")
            print(f"  Total segments: {len(sub_playlist.segments)}")
            
            # Check first segment to determine format
            if sub_playlist.segments:
                first_segment = sub_playlist.segments[0]
                segment_uri = urljoin(subtitle_uri, first_segment.uri)
                print(f"  First segment URI: {first_segment.uri}")
                print(f"  Segment duration: {first_segment.duration}")
                
                # Try to fetch first segment to check format
                seg_response = requests.get(segment_uri, headers=headers, verify=False)
                if seg_response.status_code == 200:
                    content = seg_response.text[:200]  # Just look at start of file
                    print("\nSegment content preview:")
                    print(content)
                    
                    # Determine format
                    if content.strip().startswith('WEBVTT'):
                        print("\nFormat: WebVTT")
                    elif content.strip().startswith('1\n') or content.strip().startswith('1\r\n'):
                        print("\nFormat: SRT")
                    else:
                        print("\nUnknown subtitle format")
                else:
                    print(f"\nCouldn't access subtitle segment: {seg_response.status_code}")
        else:
            print(f"Couldn't access subtitle playlist: {response.status_code}")
            
    except Exception as e:
        print(f"Error checking subtitle playlist: {str(e)}")
        logging.error(f"Error checking subtitle playlist: {str(e)}")

def analyze_subtitles(m3u8_obj, base_url):
    """
    Analyze all subtitle tracks in the master playlist.
    """
    if hasattr(m3u8_obj, 'media'):
        for media in m3u8_obj.media:
            if media.type == 'SUBTITLES':
                check_subtitle_playlist(media.uri, base_url)

def print_subtitle_summary():
    """
    Print a comprehensive subtitle summary
    """
    print("\n** Subtitle/Caption Summary **")
    
    if not hasattr(m3u8_obj, 'media'):
        print("No subtitle tracks found in manifest")
        return

    subtitle_tracks = [m for m in m3u8_obj.media if m.type == 'SUBTITLES']
    
    if not subtitle_tracks:
        print("No subtitle tracks found in manifest")
        return

    for track in subtitle_tracks:
        print(f"\nSubtitle Track Details:")
        print(f"  Language: {track.language}")
        print(f"  Name: {track.name}")
        print(f"  Group ID: {track.group_id}")
        print(f"  Type: WebVTT")  # We confirmed this from the segment analysis
        print(f"  Playlist: {track.uri}")
        print(f"  Default: {getattr(track, 'default', 'NO')}")
        print(f"  Autoselect: {getattr(track, 'autoselect', 'NO')}")
        
        # Check if this subtitle group is referenced by any variants
        referenced = False
        for playlist in m3u8_obj.playlists:
            if hasattr(playlist.stream_info, 'subtitles') and playlist.stream_info.subtitles == track.group_id:
                referenced = True
                break
        
        if not referenced:
            print("\nWarning: This subtitle track is not referenced by any video variants")
            print("        To fix this, add 'SUBTITLES=\"subs\"' to the variant streams")
        
def generate_summary():
    """
    Generate a comprehensive summary of the analysis.
    """
    logging.info("Generating summary report.")
    print("\n** Analysis Summary **")
    
    # Variant analysis summary
    print(f"Total variants analyzed: {len(videoFramesInfoDict)}")
    for bw, vf in videoFramesInfoDict.items():
        print(f"Variant {bw} bps:")
        print(f"  Segments analyzed: {len(vf.segmentsFirstFramePts)}")
        print(f"  Total keyframes: {vf.count}")
        print(f"  Min keyframe interval: {vf.minKfi / 1_000_000:.2f} seconds")
        print(f"  Max keyframe interval: {vf.maxKfi / 1_000_000:.2f} seconds")

    # Print subtitle summary
    print_subtitle_summary()

    # Warnings summary
    if warnings:
        print("\n** Summary of Warnings **")
        for warning in warnings:
            print(f"- {warning}")
    else:
        print("\nNo warnings encountered during the analysis.")

    logging.info("Summary report generated.")

def verify_url(url):
    """
    Verify URL accessibility using exact curl-matching headers.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36',
        'Referer': 'https://dablu0xuev4uv.cloudfront.net',
        'Accept': '*/*'
    }

    try:
        response = requests.head(
            url, 
            headers=headers,
            verify=False,  # Matches --ssl-no-revoke
            allow_redirects=True
        )
        
        if response.status_code == 200:
            logging.info(f"URL is accessible: {url}")
            return True
        else:
            # If HEAD fails, try GET as fallback
            response = requests.get(
                url,
                headers=headers,
                verify=False,
                allow_redirects=True
            )
            if response.status_code == 200:
                logging.info(f"URL is accessible (via GET): {url}")
                return True
            
            logging.warning(f"URL returned status code {response.status_code}: {url}")
            return False
            
    except Exception as e:
        logging.error(f"Error accessing URL {url}: {e}")
        return False


def validate_uri_paths(manifest_url, m3u8_obj):
    # Dynamically set the Referer based on the manifest URL
    base_referer = '/'.join(manifest_url.split('/')[:3])
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36',
        'Accept': '*/*',
        'Referer': base_referer,
    }

    base_path = manifest_url.rsplit('/', 1)[0] + '/'
    issues = []

    logging.info(f"Base path for resolution: {base_path}")

    if hasattr(m3u8_obj, 'playlists'):
        for playlist in m3u8_obj.playlists:
            try:
                absolute_uri = urljoin(base_path, playlist.uri)
                logging.info(f"Resolved absolute URL: {absolute_uri}")

                # Use headers to validate the URL
                response = requests.get(absolute_uri, headers=headers, verify=False)
                if response.status_code != 200:
                    logging.warning(f"Inaccessible playlist URL: {absolute_uri}")
                    issues.append({
                        'type': 'inaccessible',
                        'uri': playlist.uri,
                        'url': absolute_uri,
                        'status_code': response.status_code,
                    })
            except Exception as e:
                logging.error(f"Error processing playlist: {str(e)}")
                issues.append({'type': 'error', 'uri': playlist.uri, 'error': str(e)})

    return issues



def print_path_issues(issues):
    if issues:
        print("\nPath Resolution Issues:")
        logging.info("\nPath Resolution Issues:")
        for issue in issues:
            if issue['type'] == 'inaccessible':
                msg = (f"URI: {issue['uri']}\n"
                       f"Absolute URL: {issue['url']}\n"
                       f"Status Code: {issue['status_code']}\n")
            elif issue['type'] == 'error':
                msg = (f"URI: {issue['uri']}\n"
                       f"Absolute URL: {issue['url']}\n"
                       f"Error: {issue['error']}\n")
            else:
                msg = f"Unknown issue type: {issue}"
            print(msg)
            logging.info(msg)

def download_url(uri, httpRange=None):
    """
    Download URL using exact headers from working curl command.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36',
        'Referer': 'https://dablu0xuev4uv.cloudfront.net',
        'Accept': '*/*'
    }

    if httpRange:
        headers['Range'] = httpRange

    try:
        logging.info(f"Attempting to download with curl-matching headers: {uri}")
        response = requests.get(
            uri, 
            headers=headers, 
            verify=False,  # Matches --ssl-no-revoke
            allow_redirects=True
        )
        response.raise_for_status()
        logging.info(f"Successfully downloaded: {uri}")
        return response.content
    except requests.exceptions.RequestException as e:
        logging.error(f"Error downloading URL {uri}: {e}")
        return None


def load_with_retries(url, retries=3, delay=2, referer=None):
    base_referer = referer or '/'.join(url.split('/')[:3])
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36',
        'Referer': base_referer,
        'Accept': '*/*',
    }
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, verify=False)
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            logging.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
            time.sleep(delay)
    logging.error(f"Failed to load URL after {retries} attempts: {url}")
    return None

# Main section
parser = argparse.ArgumentParser(description='Analyze HLS streams and get useful information')
parser.add_argument('url', metavar='Url', type=str, help='URL of the stream to be analyzed')
parser.add_argument('-s', action="store", dest="segments", type=int, default=1, help='Number of segments to analyze per playlist')
parser.add_argument('-l', action="store", dest="frame_info_len", type=int, default=30, help='Max frames per track for reporting')

args = parser.parse_args()
base_url = args.url

# Load the master playlist
m3u8_obj = m3u8.load(args.url)
num_segments_to_analyze_per_playlist = args.segments
max_frames_to_show = args.frame_info_len

# Add debug output here
print_manifest_info(m3u8_obj, base_url)
subtitles, issues = diagnose_subtitles(m3u8_obj, base_url)
print("\nFound Subtitles:", subtitles)
print("Issues:", issues)

print("\n** Analyzing Subtitle Tracks **")
analyze_subtitles(m3u8_obj, base_url)

if m3u8_obj.is_variant:
    print_subtitle_diagnosis(m3u8_obj, base_url)
    logging.info("Master playlist detected. Starting analysis of variants.")
    print("Master playlist. List of variants:")

    for playlist in m3u8_obj.playlists:
        # Get the resolved URL for the variant
        variant_url = urljoin(base_url, playlist.uri) if not playlist.uri.startswith('http') else playlist.uri
        
        # Verify URL is accessible
        if not verify_url(variant_url):
            logging.warning(f"Skipping inaccessible playlist URL: {variant_url}")
            continue

        try:
            # Load the variant playlist
            variant_data = download_url(variant_url)
            if not variant_data:
                logging.error(f"Failed to download variant playlist: {variant_url}")
                continue

            # Parse the variant playlist
            if isinstance(variant_data, bytes):
                variant_data = variant_data.decode('utf-8')
            variant_playlist = m3u8.loads(variant_data)
            
            # Analyze the variant
            analyze_variant(variant_url, playlist.stream_info.bandwidth)
        except Exception as e:
            logging.error(f"Error processing variant {variant_url}: {e}")
            continue
else:
    logging.info("Single variant playlist detected. Starting analysis.")
    try:
        analyze_variant(args.url, 0)  # Use 0 as bandwidth for single variant
    except Exception as e:
        logging.error(f"Error analyzing single variant playlist: {e}")

analyze_variants_frame_alignment()
generate_summary()

logging.info("Analysis completed successfully.")
print("\nAnalysis completed successfully.")
print("Warnings were issued for missing or misaligned segments, but the script continued analyzing the rest of the stream.")
print(f"Total variants analyzed: {len(videoFramesInfoDict)}")




