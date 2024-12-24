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
    logging.info(f"Downloading {uri}, Range: {httpRange}")
    print(f"\n\t** Downloading {uri}, Range: {httpRange} **")
    opener = urllib.request.build_opener(m3u8.getCookieProcessor())
    if httpRange is not None:
        opener.addheaders.append(('Range', httpRange))

    response = opener.open(uri)
    content = response.read()
    response.close()
    logging.info(f"Download completed for {uri}")
    return content

def analyze_variant(variant, bw):
    logging.info(f"Analyzing variant ({bw})")
    print(f"***** Analyzing variant ({bw}) *****")
    print("\n\t** Generic information **")
    logging.info(f"Variant details: Version={variant.version}, Media Sequence={variant.media_sequence}, Is Live={not variant.is_endlist}")
    print(f"\tVersion: {variant.version}")
    print(f"\tStart Media sequence: {variant.media_sequence}")
    print(f"\tIs Live: {not variant.is_endlist}")
    print(f"\tEncrypted: {variant.key is not None}")
    print(f"\tNumber of segments: {len(variant.segments)}")
    print(f"\tPlaylist duration: {get_playlist_duration(variant)}")

    start = 0
    videoFramesInfoDict[bw] = VideoFramesInfo()

    if not variant.is_endlist:
        start = max(0, len(variant.segments) - max(3, num_segments_to_analyze_per_playlist))

    for i in range(start, min(start + num_segments_to_analyze_per_playlist, len(variant.segments))):
        analyze_segment(variant.segments[i], bw, variant.media_sequence + i)

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
    logging.info(f"Analyzing segment {segment.absolute_uri} with index {segment_index} for variant {bw}")
    print(f"Analyzing segment {segment.absolute_uri} with index {segment_index} for variant {bw}")
    segment_data = bytearray(download_url(segment.absolute_uri, get_range(segment.byterange)))
    ts_parser = TSSegmentParser(segment_data)
    ts_parser.prepare()

    printFormatInfo(ts_parser)
    printTimingInfo(ts_parser, segment)
    analyzeFrames(ts_parser, bw, segment_index)

    logging.info(f"Finished processing segment {segment_index} for variant {bw}")
    print(f"Finished processing segment {segment_index} for variant {bw}\n")


def analyze_variants_frame_alignment():
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
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Origin': base_url.split('/')[2],
        'Referer': base_url
    }
    
    if hasattr(playlist, 'media'):
        for media in playlist.media:
            if media.type == 'SUBTITLES':
                subtitle_uri = media.uri
                if not subtitle_uri.startswith('http'):
                    subtitle_uri = urllib.parse.urljoin(base_url, subtitle_uri)
                
                try:
                    session = requests.Session()
                    response = session.get(subtitle_uri, headers=headers, allow_redirects=True)
                    if response.status_code == 200:
                        logging.info(f"Successfully accessed subtitle playlist: {subtitle_uri}")
                    else:
                        logging.warning(f"Subtitle playlist {subtitle_uri} returned status {response.status_code}")
                except Exception as e:
                    logging.error(f"Error accessing subtitle playlist: {str(e)}")

                captions_detected.append({
                    'language': media.language,
                    'type': media.type,
                    'uri': subtitle_uri,
                    'group_id': media.group_id,
                    'name': media.name
                })

def diagnose_subtitles(master_playlist, base_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Origin': base_url.split('/')[2],
        'Referer': base_url
    }
    
    issues = []
    subtitle_groups = {}
    logging.info("Starting subtitle diagnosis")
    
    if hasattr(master_playlist, 'media'):
        for media in master_playlist.media:
            if media.type == 'SUBTITLES':
                group_id = media.group_id
                sub_uri = media.uri
                if not sub_uri.startswith('http'):
                    sub_uri = urllib.parse.urljoin(base_url, sub_uri)
                    logging.info(f"Resolved relative subtitle URI to: {sub_uri}")
                subtitle_groups[group_id] = {
                    'uri': sub_uri,
                    'language': media.language,
                    'referenced': False
                }
                logging.info(f"Found subtitle track - Group: {group_id}, Language: {media.language}")
    
    for playlist in master_playlist.playlists:
        if hasattr(playlist.stream_info, 'subtitles'):
            group_id = playlist.stream_info.subtitles
            if group_id in subtitle_groups:
                subtitle_groups[group_id]['referenced'] = True
                logging.info(f"Subtitle group {group_id} is referenced by variant")
            else:
                msg = f"Variant references undefined subtitle group: {group_id}"
                log_warning(msg)
                issues.append(msg)
    
    for group_id, info in subtitle_groups.items():
        try:
            session = requests.Session()
            response = session.get(info['uri'], headers=headers, allow_redirects=True)
            if response.status_code != 200:
                msg = f"Subtitle playlist {info['uri']} returned status {response.status_code}"
                log_warning(msg)
                issues.append(msg)
            else:
                logging.info(f"Successfully verified subtitle playlist: {info['uri']}")
        except Exception as e:
            msg = f"Failed to load subtitle playlist {info['uri']}: {str(e)}"
            log_warning(msg)
            issues.append(msg)
    
    if not subtitle_groups:
        log_warning("No subtitle tracks found in master playlist")
    
    return issues

def print_subtitle_diagnosis(master_playlist, base_url):
    """
    Print diagnostic results
    """
    print("\n** Subtitle Diagnostic Results **")
    issues = diagnose_subtitles(master_playlist, base_url)
    
    if issues:
        print("\nPotential Issues Found:")
        for issue in issues:
            print(f"- {issue}")
            logging.warning(issue)
    else:
        print("No subtitle issues detected")
        logging.info("No subtitle issues detected")
        
def generate_summary():
    logging.info("Generating summary report.")
    print("\n** Analysis Summary **")
    print(f"Total variants analyzed: {len(videoFramesInfoDict)}")
    for bw, vf in videoFramesInfoDict.items():
        print(f"Variant {bw} bps:")
        print(f"  Segments analyzed: {len(vf.segmentsFirstFramePts)}")
        print(f"  Total keyframes: {vf.count}")
        print(f"  Min keyframe interval: {vf.minKfi / 1_000_000:.2f} seconds")
        print(f"  Max keyframe interval: {vf.maxKfi / 1_000_000:.2f} seconds")

    if captions_detected:
        print("\n** Captions Summary **")
        for caption in captions_detected:
            print(f"- Language: {caption['language']}, Type: {caption['type']}, URI: {caption['uri']}")
    else:
        print("\nNo captions or subtitle tracks detected.")

    if warnings:
        print("\n** Summary of Warnings **")
        for warning in warnings:
            print(f"- {warning}")
    else:
        print("\nNo warnings encountered during the analysis.")
    logging.info("Summary report generated.")

def validate_uri_paths(manifest_url, m3u8_obj):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Origin': manifest_url.split('/')[2],
        'Referer': manifest_url
    }
    
    base_path = manifest_url.rsplit('/', 1)[0] + '/'
    issues = []
    
    # Log manifest version and base path
    logging.info(f"Manifest version: {m3u8_obj.version}")
    logging.info(f"Base path for resolution: {base_path}")
    
    # Check all URIs in playlists
    if hasattr(m3u8_obj, 'playlists'):
        for playlist in m3u8_obj.playlists:
            try:
                # Compare both resolution methods
                direct_url = playlist.absolute_uri
                relative_url = urllib.parse.urljoin(base_path, playlist.uri)
                
                logging.info(f"Checking playlist URI:")
                logging.info(f"Direct URL: {direct_url}")
                logging.info(f"Resolved relative URL: {relative_url}")
                
                # Try both URLs
                direct_response = requests.head(direct_url, headers=headers)
                relative_response = requests.head(relative_url, headers=headers)
                
                if direct_response.status_code != relative_response.status_code:
                    issues.append({
                        'type': 'resolution_mismatch',
                        'direct_url': direct_url,
                        'direct_status': direct_response.status_code,
                        'relative_url': relative_url,
                        'relative_status': relative_response.status_code
                    })
                    
            except Exception as e:
                logging.error(f"Error checking URIs: {str(e)}")
                issues.append({
                    'type': 'error',
                    'uri': playlist.uri,
                    'error': str(e)
                })
    
    return issues

def print_path_issues(issues):
    if issues:
        msg = "\nPath Resolution Issues:"
        print(msg)
        logging.info(msg)  
        for issue in issues:
            msg = f"\nOriginal path: {issue['original_path']}\nResolved to: {issue['resolved_url']}"
            print(msg)
            logging.info(msg)  

def download_url(uri, httpRange=None):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Origin': args.url.split('/')[2],
        'Referer': args.url
    }
    
    session = requests.Session()
    if httpRange:
        headers['Range'] = httpRange
    
    response = session.get(uri, headers=headers)
    return response.content


# MAIN
parser = argparse.ArgumentParser(description='Analyze HLS streams and get useful information')
parser.add_argument('url', metavar='Url', type=str, help='URL of the stream to be analyzed')
parser.add_argument('-s', action="store", dest="segments", type=int, default=1, help='Number of segments to analyze per playlist')
parser.add_argument('-l', action="store", dest="frame_info_len", type=int, default=30, help='Max frames per track for reporting')

args = parser.parse_args()
base_url = args.url

m3u8_obj = m3u8.load(args.url)
num_segments_to_analyze_per_playlist = args.segments
max_frames_to_show = args.frame_info_len

path_issues = validate_uri_paths(args.url, m3u8_obj)
print_path_issues(path_issues)

if m3u8_obj.is_variant:
    print_subtitle_diagnosis(m3u8_obj, base_url)
    logging.info("Master playlist detected. Starting analysis of variants.")
    print("Master playlist. List of variants:")
    
    # Check for subtitles in master playlist first
    check_for_captions(m3u8_obj, base_url)
    print_subtitle_diagnosis(m3u8_obj, base_url)
    
    for playlist in m3u8_obj.playlists:
        print(f"\tPlaylist: {playlist.absolute_uri}, bw: {playlist.stream_info.bandwidth}")
    print("")
    for playlist in m3u8_obj.playlists:
        variant_playlist = m3u8.load(playlist.absolute_uri)
        analyze_variant(variant_playlist, playlist.stream_info.bandwidth)
else:
    logging.info("Single variant playlist detected. Starting analysis.")
    analyze_variant(m3u8_obj, 0)
    check_for_captions(m3u8_obj, base_url)

analyze_variants_frame_alignment()
generate_summary()

logging.info("Analysis completed successfully.")
print("\nAnalysis completed successfully.")
print("Warnings were issued for missing or misaligned segments, but the script continued analyzing the rest of the stream.")
print(f"Total variants analyzed: {len(videoFramesInfoDict)}")




