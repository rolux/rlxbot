#!/usr/bin/env python3

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
TIMELINES_DIR = ROOT / "timelines"
CUTS_DIR = ROOT / "cuts"
SCENES_DIR = ROOT / "scenes"
TIMELINE_HEIGHT = 256
OVERVIEW_WIDTH = 3840
OVERVIEW_HEIGHT = 16
TIMELINE_VARIANTS = ("mean", "slitscan")


EDITOR_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scene Editor</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #202020;
    --panel: #202020;
    --card: #21242a;
    --card-active: #606060;
    --line: #343840;
    --text: #f3f4f6;
    --muted: #939aa5;
    --accent: #f1b84b;
    --danger: #eb6a64;
  }
  * { box-sizing: border-box; }
  html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; }
  body {
    background: var(--bg);
    color: var(--text);
    font: 13px/1.35 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  button, input { font: inherit; }
  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 256px;
    width: 100vw;
    height: 100vh;
  }
  .main {
    min-width: 0;
    display: grid;
    grid-template-rows: auto minmax(64px, 1fr) 16px;
    row-gap: 8px;
    padding: 0 0 8px;
    background: var(--bg);
  }
  .video-stage {
    position: relative;
    min-height: 0;
    width: 100%;
    aspect-ratio: var(--video-aspect, 16 / 9);
    max-height: calc(100vh - 104px);
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }
  .project-loading {
    position: absolute;
    inset: 0;
    z-index: 20;
    display: grid;
    place-items: center;
    color: #d9dce1;
    background: rgba(32, 32, 32, .72);
    font-size: 13px;
    pointer-events: none;
  }
  .project-loading[hidden] { display: none; }
  video {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
    object-position: center;
    background: #000;
    cursor: pointer;
  }
  .detail-timeline, .overview-timeline {
    position: relative;
    overflow: hidden;
    user-select: none;
    touch-action: none;
    background: var(--bg);
    cursor: ew-resize;
  }
  .timeline-track {
    position: relative;
    display: flex;
    width: max-content;
    height: 100%;
    will-change: transform;
  }
  .timeline-track > img.timeline-tile {
    display: block;
    flex: none;
    height: 100%;
    image-rendering: auto;
    pointer-events: none;
  }
  .timeline-spacer { flex: none; height: 100%; }
  .timeline-icon {
    position: absolute;
    z-index: 10;
    width: auto;
    height: auto;
    image-rendering: pixelated;
    pointer-events: none;
  }
  .player-icon { top: 0; margin-left: -7px; }
  .keyframe-icon { top: 0; margin-left: -5px; }
  .cut-icon { top: 0; margin-left: -3px; }
  #detailPointer { left: 50%; }
  .overview-timeline { cursor: pointer; }
  .overview-row {
    display: grid;
    grid-template-columns: 80px minmax(0, 1fr) 80px;
    gap: 8px;
    padding: 0 8px;
    min-width: 0;
  }
  #overviewImage {
    display: block;
    width: 100%;
    height: 16px;
  }
  .volume-display,
  .current-timecode {
    display: block;
    color: #d9dce1;
    text-align: center;
    font: 11px/16px ui-monospace, SFMono-Regular, Menlo, monospace;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .current-timecode {
    cursor: pointer;
    user-select: none;
  }
  .current-timecode:hover { color: #fff; }
  .sidebar {
    min-width: 0;
    display: grid;
    grid-template-rows: 96px minmax(0, 1fr) 64px;
    overflow: hidden;
    background: var(--panel);
    border-left: 1px solid #2b2e34;
  }
  #sceneList {
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px;
    overflow-y: auto;
    scrollbar-color: #4d525c transparent;
  }
  .sidebar-toolbar {
    z-index: 20;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    padding: 8px;
    background: rgba(32, 32, 32, .96);
    backdrop-filter: blur(8px);
  }
  .sidebar-toolbar.top {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 76px;
    grid-template-rows: auto auto;
    border-bottom: 1px solid #30343b;
  }
  .sidebar-toolbar.bottom {
    justify-content: center;
    border-top: 1px solid #30343b;
  }
  .metadata-actions {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .toolbar-button {
    padding: 4px 8px;
    border: 1px solid #555b65;
    border-radius: 4px;
    color: #d9dce1;
    background: #292c31;
    font-size: 11px;
    font-weight: 500;
    cursor: pointer;
  }
  .toolbar-button:disabled { opacity: .35; cursor: default; }
  .video-select {
    flex: 1;
    width: auto;
    min-width: 0;
    padding: 4px 7px;
    border: 1px solid #555b65;
    border-radius: 4px;
    color: #d9dce1;
    background: #292c31;
    font-size: 11px;
  }
  .timeline-mode-select {
    flex: none;
    width: 76px;
    padding: 4px 5px;
    border: 1px solid #555b65;
    border-radius: 4px;
    color: #d9dce1;
    background: #292c31;
    font-size: 11px;
  }
  .detail-scroll {
    position: absolute;
    inset: 0;
    overflow: hidden;
  }
  .scene-card {
    position: relative;
    flex: none;
    margin: 0;
    padding: 8px;
    overflow: hidden;
    background: transparent;
  }
  .scene-card.active { background: var(--card-active); }
  .scene-header {
    display: flex;
    align-items: baseline;
    gap: 5px;
    min-width: 0;
  }
  .scene-number {
    flex: none;
    color: var(--text);
    font-size: 15px;
    font-weight: 600;
  }
  .scene-card.excluded .scene-number,
  .scene-card.excluded .scene-meta { color: #727985; }
  .scene-title {
    position: relative;
    top: 1px;
    flex: 1;
    min-width: 0;
    margin: 0 0 2px;
    padding: 1px 0 3px;
    color: var(--text);
    background: transparent;
    border: 0;
    border-bottom: 1px solid transparent;
    outline: 0;
    font-size: 15px;
    font-weight: 600;
  }
  .scene-title:focus { border-color: #727985; }
  .scene-location-row {
    display: flex;
    align-items: baseline;
    gap: 6px;
    min-width: 0;
    margin: 0 0 8px;
    color: var(--muted);
    font-size: 11px;
  }
  .scene-location-label { flex: none; }
  .scene-location {
    flex: 1;
    min-width: 0;
    margin: 0;
    padding: 1px 0 2px;
    border: 0;
    border-bottom: 1px solid transparent;
    outline: 0;
    color: #c4c8cf;
    background: transparent;
    font: inherit;
  }
  .scene-location:focus { border-color: #727985; }
  .thumbnail {
    position: relative;
    width: 100%;
    aspect-ratio: 16 / 9;
    overflow: hidden;
    border-radius: 5px;
    background: #111317;
    border: 1px solid #30343b;
    cursor: pointer;
  }
  .thumbnail img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thumbnail-placeholder {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    color: #737a85;
    font-size: 11px;
    letter-spacing: .04em;
    text-transform: uppercase;
  }
  .scene-meta {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 4px;
    margin-top: 7px;
    color: var(--muted);
    font: 11px/1.25 ui-monospace, SFMono-Regular, Menlo, monospace;
    font-variant-numeric: tabular-nums;
  }
  .scene-actions {
    display: none;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 3px;
    margin-top: 8px;
  }
  .scene-card.active .scene-actions { display: grid; }
  .scene-actions button {
    min-width: 0;
    min-height: 24px;
    padding: 2px 0;
    border: 1px solid #444a54;
    border-radius: 4px;
    color: #d9dce1;
    background: #24272c;
    font: 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
    cursor: pointer;
  }
  .scene-actions button:hover:not(:disabled) { background: #30343a; }
  .scene-actions button:disabled { opacity: .3; cursor: default; }
  .loading { padding: 16px; color: var(--muted); }
</style>
</head>
<body>
<div class="workspace">
  <main class="main">
    <div class="video-stage">
      <video id="video" preload="metadata"></video>
      <div class="project-loading" id="projectLoading">Loading video…</div>
    </div>
    <div class="detail-timeline" id="detailTimeline">
      <div class="detail-scroll" id="detailScroll">
        <div class="timeline-track" id="timelineTrack"></div>
      </div>
      <img class="timeline-icon player-icon" id="detailPointer" src="/editor/player.png" alt="">
    </div>
    <div class="overview-row">
      <output class="volume-display" id="volumeDisplay" title="Volume">100</output>
      <div class="overview-timeline" id="overviewTimeline">
        <img id="overviewImage" alt="Complete video timeline">
        <img class="timeline-icon player-icon" id="overviewPointer" src="/editor/player.png" alt="">
      </div>
      <output class="current-timecode" id="currentTimecode" title="Show current frame">00:00.000</output>
    </div>
  </main>
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-toolbar top">
      <select class="video-select" id="videoSelect" disabled aria-label="Prepared video"><option>Loading video…</option></select>
      <select class="timeline-mode-select" id="timelineModeSelect" aria-label="Timeline representation">
        <option value="mean">Mean</option>
        <option value="slitscan">Slitscan</option>
      </select>
      <div class="metadata-actions">
        <button class="toolbar-button" id="loadMetadataButton">Load Metadata</button>
        <button class="toolbar-button" id="saveMetadataButton">Save Metadata</button>
      </div>
      <input id="metadataFileInput" type="file" accept=".json,application/json" hidden>
    </div>
    <div id="sceneList"><div class="loading">Loading scenes…</div></div>
    <div class="sidebar-toolbar bottom">
      <button class="toolbar-button" id="selectCurrentButton" disabled>Select This Scene</button>
    </div>
  </aside>
</div>
<script>
(() => {
  const video = document.getElementById('video');
  const projectLoading = document.getElementById('projectLoading');
  const detail = document.getElementById('detailTimeline');
  const detailScroll = document.getElementById('detailScroll');
  const track = document.getElementById('timelineTrack');
  const overview = document.getElementById('overviewTimeline');
  const overviewImage = document.getElementById('overviewImage');
  const overviewPointer = document.getElementById('overviewPointer');
  const volumeDisplay = document.getElementById('volumeDisplay');
  const currentTimecode = document.getElementById('currentTimecode');
  const sidebar = document.getElementById('sidebar');
  const sceneList = document.getElementById('sceneList');
  const videoSelect = document.getElementById('videoSelect');
  const timelineModeSelect = document.getElementById('timelineModeSelect');
  const loadMetadataButton = document.getElementById('loadMetadataButton');
  const saveMetadataButton = document.getElementById('saveMetadataButton');
  const metadataFileInput = document.getElementById('metadataFileInput');
  const selectCurrentButton = document.getElementById('selectCurrentButton');

  let project;
  let scenes = [];
  let currentFrame = 0;
  let dragging = false;
  let dragStartX = 0;
  let dragStartScroll = 0;
  let movedDuringDrag = false;
  let selectedSceneIndex = null;
  const savedTimelineMode = localStorage.getItem('rlxbot:timeline-mode');
  let timelineMode = savedTimelineMode === 'slitscan' || savedTimelineMode === 'slit_scan' ? 'slitscan' : 'mean';
  localStorage.setItem('rlxbot:timeline-mode', timelineMode);
  let previousVolume = 1;
  let timeReadoutMode = 'timecode';
  let dirty = false;

  const sceneIdPrefix = 'T3/';
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const fps = () => project.frame_rate_num / project.frame_rate_den;

  function frameTimecode(frame) {
    const rate = Math.round(fps());
    const totalSeconds = Math.floor(frame / rate);
    const ff = frame % rate;
    const ss = totalSeconds % 60;
    const mm = Math.floor(totalSeconds / 60) % 60;
    const hh = Math.floor(totalSeconds / 3600);
    return [hh, mm, ss, ff].map(value => String(value).padStart(2, '0')).join(':');
  }

  function shortTimecode(frame) {
    const totalMilliseconds = Math.round((frame * 1000) / fps());
    const milliseconds = totalMilliseconds % 1000;
    const totalSeconds = Math.floor(totalMilliseconds / 1000);
    const seconds = totalSeconds % 60;
    const minutes = Math.floor(totalSeconds / 60);
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(milliseconds).padStart(3, '0')}`;
  }

  function seekFrame(frame, center = true) {
    currentFrame = clamp(Math.round(frame), 0, project.frame_count - 1);
    video.currentTime = (currentFrame + 0.5) / fps();
    updatePlayheads(center);
    updateActiveScene();
  }

  function togglePlayback() {
    if (!video.paused) {
      video.pause();
      return;
    }
    if (video.ended || video.currentTime >= video.duration) seekFrame(0);
    video.play();
  }

  function updateVolumeDisplay() {
    volumeDisplay.value = String(video.muted ? 0 : Math.round(video.volume * 100));
  }

  function setVolume(percent) {
    const clamped = clamp(Math.round(percent / 10) * 10, 0, 100);
    if (clamped === 0) {
      if (!video.muted && video.volume > 0) previousVolume = video.volume;
      video.muted = true;
    } else {
      video.volume = clamped / 100;
      video.muted = false;
      previousVolume = video.volume;
    }
    updateVolumeDisplay();
  }

  function toggleMute() {
    if (video.muted || video.volume === 0) {
      video.volume = previousVolume > 0 ? previousVolume : 1;
      video.muted = false;
    } else {
      previousVolume = video.volume;
      video.muted = true;
    }
    updateVolumeDisplay();
  }

  function updatePlayheads(center) {
    if (center && !dragging) detailScroll.scrollLeft = currentFrame;
    overviewPointer.style.left = `${(currentFrame / Math.max(1, project.frame_count - 1)) * 100}%`;
    currentTimecode.value = timeReadoutMode === 'timecode'
      ? shortTimecode(currentFrame)
      : `Frame ${currentFrame}`;
    currentTimecode.title = timeReadoutMode === 'timecode' ? 'Show current frame' : 'Show timecode';
  }

  function currentSceneIndex() {
    return scenes.findIndex(scene => currentFrame >= scene.start_frame && currentFrame < scene.end_frame);
  }

  function includedNumber(index) {
    if (scenes[index].keyframe_frame === null) return '';
    let number = 0;
    for (let i = 0; i <= index; i += 1) {
      if (scenes[i].keyframe_frame !== null) number += 1;
    }
    return `${sceneIdPrefix}${number}`;
  }

  function markDirty() {
    dirty = true;
    if (!project) return;
    localStorage.setItem(`rlxbot:${project.video_name}`, JSON.stringify({
      version: 1,
      frame_count: project.frame_count,
      scenes,
    }));
  }

  function normalizeScenes(source) {
    if (!Array.isArray(source) || source.length === 0) throw new Error('Metadata contains no scenes.');
    return source.map((scene, index) => {
      const startFrame = Number.isInteger(scene.start_frame) ? scene.start_frame : scene.in_frame;
      const endFrame = Number.isInteger(scene.end_frame) ? scene.end_frame
        : Number.isInteger(scene.out_frame) ? scene.out_frame + 1 : null;
      let keyframe = null;
      if (Object.prototype.hasOwnProperty.call(scene, 'keyframe_frame')) keyframe = scene.keyframe_frame;
      else if (Object.prototype.hasOwnProperty.call(scene, 'selected_frame')) keyframe = scene.selected_frame;
      else if (Object.prototype.hasOwnProperty.call(scene, 'marker_frame')) keyframe = scene.marker_frame;
      if (!Number.isInteger(startFrame) || !Number.isInteger(endFrame) || startFrame >= endFrame) {
        throw new Error(`Scene ${index + 1} has invalid frame boundaries.`);
      }
      if (keyframe !== null && (!Number.isInteger(keyframe) || keyframe < startFrame || keyframe >= endFrame)) {
        throw new Error(`Scene ${index + 1} has a keyframe outside its boundaries.`);
      }
      return {
        start_frame: startFrame,
        end_frame: endFrame,
        title: typeof scene.title === 'string' ? scene.title : 'Untitled',
        location: typeof scene.location === 'string' ? scene.location : 't.b.d.',
        keyframe_frame: keyframe,
      };
    });
  }

  function validateSceneSequence(candidate, frameCount) {
    if (candidate[0].start_frame !== 0) throw new Error('The first scene must start at frame 0.');
    for (let index = 1; index < candidate.length; index += 1) {
      if (candidate[index].start_frame !== candidate[index - 1].end_frame) {
        throw new Error(`Scenes ${index} and ${index + 1} are not contiguous.`);
      }
    }
    if (candidate.at(-1).end_frame !== frameCount) {
      throw new Error(`Metadata ends at frame ${candidate.at(-1).end_frame}, but this video has ${frameCount} frames.`);
    }
  }

  function renderTimelineOverlays() {
    const pad = detail.clientWidth / 2;
    const syncIcons = (className, source, frames) => {
      const icons = [...track.querySelectorAll(`.${className}`)];
      frames.forEach((frame, index) => {
        let icon = icons[index];
        if (!icon) {
          icon = document.createElement('img');
          icon.className = `timeline-icon ${className}`;
          icon.src = source;
          icon.alt = '';
          track.appendChild(icon);
        }
        icon.dataset.frame = frame;
        icon.style.left = `${pad + frame}px`;
      });
      icons.slice(frames.length).forEach(icon => icon.remove());
    };

    syncIcons('cut-icon', '/editor/cut.png', scenes.slice(0, -1).map(scene => scene.end_frame));
    syncIcons(
      'keyframe-icon',
      '/editor/keyframe.png',
      scenes.filter(scene => scene.keyframe_frame !== null).map(scene => scene.keyframe_frame),
    );
  }

  function setKeyframe(index) {
    const scene = scenes[index];
    if (currentFrame < scene.start_frame || currentFrame >= scene.end_frame) return;
    selectedSceneIndex = index;
    scene.keyframe_frame = currentFrame;
    renderSidebar();
    renderTimelineOverlays();
    markDirty();
  }

  function clearKeyframe(index) {
    selectedSceneIndex = index;
    scenes[index].keyframe_frame = null;
    renderSidebar();
    renderTimelineOverlays();
    markDirty();
  }

  function canSplit(index) {
    if (index < 0 || index >= scenes.length) return false;
    const scene = scenes[index];
    return currentFrame > scene.start_frame && currentFrame < scene.end_frame;
  }

  function splitHere(index) {
    if (!canSplit(index)) return;
    const scene = scenes[index];
    const originalKeyframe = scene.keyframe_frame;
    let left;
    let right;

    if (scene.keyframe_frame === null) {
      left = {...scene, end_frame: currentFrame};
      right = {
        start_frame: currentFrame,
        end_frame: scene.end_frame,
        title: 'Untitled',
        location: 't.b.d.',
        keyframe_frame: null,
      };
    } else if (scene.keyframe_frame < currentFrame) {
      left = {...scene, end_frame: currentFrame};
      right = {
        start_frame: currentFrame,
        end_frame: scene.end_frame,
        title: 'Untitled',
        location: 't.b.d.',
        keyframe_frame: currentFrame,
      };
    } else {
      left = {
        start_frame: scene.start_frame,
        end_frame: currentFrame,
        title: 'Untitled',
        location: 't.b.d.',
        keyframe_frame: scene.start_frame,
      };
      right = {...scene, start_frame: currentFrame};
    }

    scenes.splice(index, 1, left, right);
    selectedSceneIndex = originalKeyframe === null || originalKeyframe < currentFrame ? index : index + 1;
    renderSidebar();
    renderTimelineOverlays();
    markDirty();
  }

  function canSetStart(index) {
    if (index <= 0) return false;
    return currentFrame > scenes[index - 1].start_frame &&
      currentFrame < scenes[index].end_frame &&
      currentFrame !== scenes[index].start_frame;
  }

  function canSetEnd(index) {
    if (index >= scenes.length - 1) return false;
    return currentFrame > scenes[index].start_frame &&
      currentFrame < scenes[index + 1].end_frame &&
      currentFrame !== scenes[index].end_frame;
  }

  function setStartHere(index) {
    if (!canSetStart(index)) return;
    const previous = scenes[index - 1];
    const scene = scenes[index];
    previous.end_frame = currentFrame;
    scene.start_frame = currentFrame;
    if (previous.keyframe_frame !== null && previous.keyframe_frame >= currentFrame) {
      previous.keyframe_frame = currentFrame - 1;
    }
    if (scene.keyframe_frame !== null && scene.keyframe_frame < currentFrame) {
      scene.keyframe_frame = currentFrame;
    }
    renderSidebar();
    renderTimelineOverlays();
    markDirty();
  }

  function setEndHere(index) {
    if (!canSetEnd(index)) return;
    const scene = scenes[index];
    const next = scenes[index + 1];
    scene.end_frame = currentFrame;
    next.start_frame = currentFrame;
    if (scene.keyframe_frame !== null && scene.keyframe_frame >= currentFrame) {
      scene.keyframe_frame = currentFrame - 1;
    }
    if (next.keyframe_frame !== null && next.keyframe_frame < currentFrame) {
      next.keyframe_frame = currentFrame;
    }
    renderSidebar();
    renderTimelineOverlays();
    markDirty();
  }

  function mergeWithPrevious(index) {
    if (index <= 0) return;
    const scene = scenes[index];
    scenes.splice(index - 1, 2, {...scene, start_frame: scenes[index - 1].start_frame});
    selectedSceneIndex = index - 1;
    renderSidebar();
    renderTimelineOverlays();
    markDirty();
  }

  function mergeWithNext(index) {
    if (index >= scenes.length - 1) return;
    const scene = scenes[index];
    scenes.splice(index, 2, {...scene, end_frame: scenes[index + 1].end_frame});
    selectedSceneIndex = index;
    renderSidebar();
    renderTimelineOverlays();
    markDirty();
  }

  function renderSidebar() {
    const active = selectedSceneIndex;
    sceneList.replaceChildren();
    scenes.forEach((scene, index) => {
      const card = document.createElement('section');
      card.className = `scene-card${scene.keyframe_frame === null ? ' excluded' : ''}${index === active ? ' active' : ''}`;
      card.dataset.sceneIndex = index;
      card.addEventListener('click', event => {
        if (event.metaKey) {
          event.preventDefault();
          selectedSceneIndex = null;
          renderSidebar();
          return;
        }
        if (event.target.closest('input,button,.thumbnail')) return;
        selectedSceneIndex = index;
        renderSidebar();
        seekFrame(scene.start_frame);
      });

      const header = document.createElement('div');
      header.className = 'scene-header';
      if (scene.keyframe_frame !== null) {
        const number = document.createElement('div');
        number.className = 'scene-number';
        number.textContent = `[${includedNumber(index)}]`;
        header.appendChild(number);
      }

      const title = document.createElement('input');
      title.className = 'scene-title';
      title.value = scene.title;
      title.placeholder = scene.keyframe_frame === null ? 'Excluded scene' : 'Scene title';
      title.setAttribute('aria-label', `Title for scene ${index + 1}`);
      title.addEventListener('focus', () => {
        selectedSceneIndex = index;
        updateActiveScene();
      });
      title.addEventListener('input', () => { scene.title = title.value; markDirty(); });
      header.appendChild(title);
      card.appendChild(header);

      const locationRow = document.createElement('label');
      locationRow.className = 'scene-location-row';
      const locationLabel = document.createElement('span');
      locationLabel.className = 'scene-location-label';
      locationLabel.textContent = 'Location';
      const location = document.createElement('input');
      location.className = 'scene-location';
      location.value = scene.location;
      location.placeholder = 't.b.d.';
      location.setAttribute('aria-label', `Location for scene ${index + 1}`);
      location.addEventListener('focus', () => {
        selectedSceneIndex = index;
        updateActiveScene();
      });
      location.addEventListener('input', () => { scene.location = location.value; markDirty(); });
      locationRow.append(locationLabel, location);
      card.appendChild(locationRow);

      const thumbnail = document.createElement('div');
      thumbnail.className = 'thumbnail';
      if (scene.keyframe_frame !== null) {
        const image = document.createElement('img');
        image.src = `${project.frame_url_prefix}${scene.keyframe_frame}.png`;
        image.loading = 'lazy';
        image.alt = scene.title || `Keyframe ${scene.keyframe_frame}`;
        image.addEventListener('click', event => {
          if (event.metaKey) return;
          event.stopPropagation();
          selectedSceneIndex = index;
          renderSidebar();
          seekFrame(scene.keyframe_frame);
        });
        thumbnail.appendChild(image);
      } else {
        const placeholder = document.createElement('div');
        placeholder.className = 'thumbnail-placeholder';
        placeholder.textContent = 'No frame selected';
        thumbnail.appendChild(placeholder);
      }

      card.appendChild(thumbnail);

      const meta = document.createElement('div');
      meta.className = 'scene-meta';
      const range = `${shortTimecode(scene.start_frame)} - ${shortTimecode(scene.end_frame - 1)}`;
      if (scene.keyframe_frame !== null) {
        meta.innerHTML = `<span>${range}</span><span>Frame ${scene.keyframe_frame}</span>`;
      } else {
        meta.innerHTML = `<span>${range}</span><span>No frame</span>`;
      }
      card.appendChild(meta);

      const actions = document.createElement('div');
      actions.className = 'scene-actions';
      const addAction = (label, title, className, disabled, handler) => {
        const button = document.createElement('button');
        button.className = className;
        button.textContent = label;
        button.title = title;
        button.setAttribute('aria-label', title);
        button.disabled = disabled;
        button.addEventListener('click', event => { event.stopPropagation(); handler(); });
        actions.appendChild(button);
      };
      addAction('^I', 'Set in here', 'set-start', !canSetStart(index), () => setStartHere(index));
      addAction('^O', 'Set out here', 'set-end', !canSetEnd(index), () => setEndHere(index));
      addAction('^S', 'Split here', 'split-scene', !canSplit(index), () => splitHere(index));
      addAction('<M', 'Merge with previous', 'merge-previous', index === 0, () => mergeWithPrevious(index));
      addAction('M>', 'Merge with next', 'merge-next', index === scenes.length - 1, () => mergeWithNext(index));
      addAction('K+', 'Set keyframe here', 'set-keyframe', currentFrame < scene.start_frame || currentFrame >= scene.end_frame, () => setKeyframe(index));
      addAction('K-', 'Remove keyframe', 'clear-keyframe', scene.keyframe_frame === null, () => clearKeyframe(index));
      card.appendChild(actions);
      sceneList.appendChild(card);
    });
  }

  function updateActiveScene() {
    const active = selectedSceneIndex;
    const current = currentSceneIndex();
    selectCurrentButton.disabled = current === -1 || current === selectedSceneIndex;
    sceneList.querySelectorAll('.scene-card').forEach((card, index) => {
      card.classList.toggle('active', index === active);
      card.querySelector('.set-start').disabled = !canSetStart(index);
      card.querySelector('.set-end').disabled = !canSetEnd(index);
      card.querySelector('.merge-previous').disabled = index === 0;
      card.querySelector('.merge-next').disabled = index === scenes.length - 1;
      card.querySelector('.set-keyframe').disabled = currentFrame < scenes[index].start_frame || currentFrame >= scenes[index].end_frame;
      card.querySelector('.clear-keyframe').disabled = scenes[index].keyframe_frame === null;
      card.querySelector('.split-scene').disabled = !canSplit(index);
    });
  }

  function buildTimeline() {
    const variant = project.timeline_variants[timelineMode];
    track.replaceChildren();
    const spacerBefore = document.createElement('div');
    const spacerAfter = document.createElement('div');
    spacerBefore.className = spacerAfter.className = 'timeline-spacer';
    spacerBefore.id = 'spacerBefore';
    spacerAfter.id = 'spacerAfter';
    track.appendChild(spacerBefore);
    variant.tiles.forEach(tile => {
      const image = document.createElement('img');
      image.className = 'timeline-tile';
      image.src = tile.url;
      image.width = tile.width;
      image.height = project.timeline_height;
      image.alt = '';
      track.appendChild(image);
    });
    track.appendChild(spacerAfter);
    updateTimelinePadding();
  }

  function updateTimelineMode() {
    const variant = project.timeline_variants[timelineMode];
    timelineModeSelect.value = timelineMode;
    overviewImage.src = variant.overview_url;
    buildTimeline();
  }

  function updateTimelinePadding() {
    const width = detail.clientWidth / 2;
    document.getElementById('spacerBefore').style.width = `${width}px`;
    document.getElementById('spacerAfter').style.width = `${width}px`;
    renderTimelineOverlays();
    updatePlayheads(true);
  }

  detail.addEventListener('pointerdown', event => {
    dragging = true;
    movedDuringDrag = false;
    dragStartX = event.clientX;
    dragStartScroll = detailScroll.scrollLeft;
    detail.setPointerCapture(event.pointerId);
  });
  detail.addEventListener('pointermove', event => {
    if (!dragging) return;
    const delta = event.clientX - dragStartX;
    if (Math.abs(delta) > 2) movedDuringDrag = true;
    detailScroll.scrollLeft = clamp(dragStartScroll - delta, 0, project.frame_count - 1);
    currentFrame = clamp(Math.round(detailScroll.scrollLeft), 0, project.frame_count - 1);
    video.currentTime = (currentFrame + 0.5) / fps();
    updatePlayheads(false);
    updateActiveScene();
  });
  detail.addEventListener('pointerup', event => {
    detail.releasePointerCapture(event.pointerId);
    dragging = false;
    if (!movedDuringDrag) {
      const bounds = detail.getBoundingClientRect();
      const clickedFrame = detailScroll.scrollLeft + event.clientX - bounds.left - bounds.width / 2;
      seekFrame(clickedFrame);
    } else {
      seekFrame(detailScroll.scrollLeft);
    }
  });

  overview.addEventListener('click', event => {
    const bounds = overview.getBoundingClientRect();
    seekFrame(((event.clientX - bounds.left) / bounds.width) * (project.frame_count - 1));
  });

  currentTimecode.addEventListener('click', () => {
    timeReadoutMode = timeReadoutMode === 'timecode' ? 'frame' : 'timecode';
    updatePlayheads(false);
  });

  video.addEventListener('click', togglePlayback);
  video.addEventListener('volumechange', updateVolumeDisplay);

  timelineModeSelect.addEventListener('change', () => {
    timelineMode = timelineModeSelect.value;
    localStorage.setItem('rlxbot:timeline-mode', timelineMode);
    updateTimelineMode();
  });

  function selectCurrentScene() {
    const index = currentSceneIndex();
    if (index === -1) return;
    selectedSceneIndex = index;
    updateActiveScene();
    sceneList.querySelector(`.scene-card[data-scene-index="${index}"]`)?.scrollIntoView({block: 'nearest'});
  }

  selectCurrentButton.addEventListener('click', selectCurrentScene);

  loadMetadataButton.addEventListener('click', () => {
    metadataFileInput.value = '';
    metadataFileInput.click();
  });

  metadataFileInput.addEventListener('change', async () => {
    const file = metadataFileInput.files?.[0];
    if (!file) return;
    try {
      const metadata = JSON.parse(await file.text());
      if (Number.isInteger(metadata.frame_count) && metadata.frame_count !== project.frame_count) {
        throw new Error(`Metadata has ${metadata.frame_count} frames, but this video has ${project.frame_count}.`);
      }
      const importedScenes = normalizeScenes(metadata.scenes);
      validateSceneSequence(importedScenes, project.frame_count);
      scenes = importedScenes;
      selectedSceneIndex = null;
      renderSidebar();
      renderTimelineOverlays();
      updateActiveScene();
      markDirty();
    } catch (error) {
      window.alert(`Could not load metadata: ${error.message}`);
    }
  });

  saveMetadataButton.addEventListener('click', () => {
    const missingTitle = scenes.findIndex(scene => scene.keyframe_frame !== null && !scene.title.trim());
    if (missingTitle !== -1) {
      window.alert(`Scene ${includedNumber(missingTitle)} has a keyframe but no title.`);
      sceneList.querySelector(`.scene-card[data-scene-index="${missingTitle}"]`)?.scrollIntoView({block: 'center'});
      sceneList.querySelector(`.scene-card[data-scene-index="${missingTitle}"] .scene-title`)?.focus();
      return;
    }
    let included = 0;
    const exportedScenes = scenes.map(scene => {
      const exported = {
        title: scene.title,
        location: scene.location,
        in_frame: scene.start_frame,
        out_frame: scene.end_frame - 1,
        keyframe_frame: scene.keyframe_frame,
        in_timecode: frameTimecode(scene.start_frame),
        out_timecode: frameTimecode(scene.end_frame - 1),
        keyframe_timecode: scene.keyframe_frame === null ? null : frameTimecode(scene.keyframe_frame),
      };
      if (scene.keyframe_frame === null) return exported;
      included += 1;
      return {id: `${sceneIdPrefix}${included}`, ...exported};
    });
    const output = {
      version: 1,
      video: project.video_name,
      frame_rate: `${project.frame_rate_num}/${project.frame_rate_den}`,
      frame_count: project.frame_count,
      scenes: exportedScenes,
    };
    const blob = new Blob([JSON.stringify(output, null, 2) + '\n'], {type: 'application/json'});
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = project.output_filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 0);
    dirty = false;
  });

  window.addEventListener('beforeunload', event => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });

  document.addEventListener('keydown', event => {
    if (event.target.matches('input,textarea')) return;
    const keyframeShortcut = event.key.toLowerCase() === 'k' && !event.metaKey && !event.ctrlKey && !event.altKey;
    const volumeShortcut = ['=', '+', '-', '0'].includes(event.key) && !event.metaKey && !event.ctrlKey && !event.altKey;
    if ([' ', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', ',', '.', '/'].includes(event.key)) {
      event.preventDefault();
    }
    if (event.key === '/') {
      selectCurrentScene();
      return;
    }
    if (volumeShortcut) {
      event.preventDefault();
      if (event.key === '0') toggleMute();
      else {
        const current = video.muted ? 0 : Math.round(video.volume * 100);
        setVolume(current + (event.key === '=' || event.key === '+' ? 10 : -10));
      }
      return;
    }
    if (keyframeShortcut) {
      event.preventDefault();
      if (selectedSceneIndex === null) return;
      if (event.shiftKey) {
        if (scenes[selectedSceneIndex].keyframe_frame !== null) clearKeyframe(selectedSceneIndex);
      } else {
        setKeyframe(selectedSceneIndex);
      }
      return;
    }
    if (event.key === ' ') togglePlayback();
    if (event.key === 'ArrowLeft' || event.key === ',') seekFrame(currentFrame - 1);
    if (event.key === 'ArrowRight' || event.key === '.') seekFrame(currentFrame + 1);
    if (event.key === 'ArrowUp') {
      const previous = scenes.map(scene => scene.start_frame).filter(frame => frame < currentFrame).pop();
      seekFrame(previous ?? 0);
    }
    if (event.key === 'ArrowDown') {
      const next = scenes.map(scene => scene.start_frame).find(frame => frame > currentFrame);
      seekFrame(next ?? project.frame_count - 1);
    }
  });

  video.addEventListener('seeked', () => {
    if (dragging) return;
    currentFrame = clamp(Math.floor(video.currentTime * fps()), 0, project.frame_count - 1);
    updatePlayheads(true);
    updateActiveScene();
  });
  video.addEventListener('timeupdate', () => {
    if (video.paused || dragging) return;
    currentFrame = clamp(Math.floor(video.currentTime * fps()), 0, project.frame_count - 1);
    updatePlayheads(true);
    updateActiveScene();
  });
  if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
    const followFrames = (_now, metadata) => {
      if (!video.paused && !dragging) {
        currentFrame = clamp(Math.floor(metadata.mediaTime * fps()), 0, project.frame_count - 1);
        updatePlayheads(true);
        updateActiveScene();
      }
      video.requestVideoFrameCallback(followFrames);
    };
    video.requestVideoFrameCallback(followFrames);
  }
  window.addEventListener('resize', updateTimelinePadding);

  async function loadProject(videoName = '') {
    video.pause();
    videoSelect.disabled = true;
    projectLoading.textContent = videoName ? `Loading ${videoName}…` : 'Loading video…';
    projectLoading.hidden = false;
    const query = videoName ? `?video=${encodeURIComponent(videoName)}` : '';
    const response = await fetch(`/api/project${query}`);
    if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
    const data = await response.json();
    project = data;
    selectedSceneIndex = null;
    currentFrame = 0;
    const stateKey = `rlxbot:${data.video_name}`;
    try {
      const saved = JSON.parse(localStorage.getItem(stateKey));
      const source = saved?.version === 1 && saved.frame_count === data.frame_count && Array.isArray(saved.scenes)
        ? saved.scenes
        : data.scenes;
      scenes = normalizeScenes(source);
      validateSceneSequence(scenes, data.frame_count);
    } catch (_error) {
      scenes = normalizeScenes(data.scenes);
      validateSceneSequence(scenes, data.frame_count);
    }
    localStorage.setItem(stateKey, JSON.stringify({version: 1, frame_count: data.frame_count, scenes}));
    updateVolumeDisplay();
    videoSelect.replaceChildren(...data.available_videos.map(name => new Option(name, name, false, name === data.video_name)));
    document.documentElement.style.setProperty('--video-aspect', `${data.video_width} / ${data.video_height}`);
    const videoReady = new Promise((resolve, reject) => {
      video.addEventListener('loadedmetadata', resolve, {once: true});
      video.addEventListener('error', () => reject(new Error(`Could not load ${data.video_name}`)), {once: true});
    });
    video.src = data.video_url;
    video.load();
    updateTimelineMode();
    await videoReady;
    renderSidebar();
    seekFrame(0);
    projectLoading.hidden = true;
    videoSelect.disabled = data.available_videos.length < 2;
  }

  videoSelect.addEventListener('change', () => {
    loadProject(videoSelect.value).catch(error => {
      projectLoading.hidden = true;
      videoSelect.disabled = false;
      sceneList.innerHTML = `<div class="loading">Could not load project: ${error}</div>`;
    });
  });

  loadProject().catch(error => {
    projectLoading.hidden = true;
    sceneList.innerHTML = `<div class="loading">Could not load project: ${error}</div>`;
  });
})();
</script>
</body>
</html>
"""


# -----------------------------------------------------------------------------
# Video information
# -----------------------------------------------------------------------------

def probe_video(filename):
    command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration",
        "-of", "json",
        str(filename),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    stream = json.loads(result.stdout)["streams"][0]
    rate = Fraction(stream["avg_frame_rate"])
    if rate <= 0:
        raise ValueError(f"Invalid frame rate reported for {filename}: {rate}")
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "frame_rate": rate,
        "frame_count": int(stream["nb_frames"]) if stream.get("nb_frames") else None,
        "duration": float(stream["duration"]) if stream.get("duration") else None,
    }


# -----------------------------------------------------------------------------
# Timeline generation
# -----------------------------------------------------------------------------

def generate_timelines(filename):
    video_path = Path(filename).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    info = probe_video(video_path)
    frames_per_tile = round(float(info["frame_rate"]) * 60)
    output_dir = TIMELINES_DIR / video_path.stem
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    command = [
        "ffmpeg",
        "-v", "error",
        "-i", str(video_path),
        "-filter_complex",
        (
            f"[0:v]format=rgb24,split=2[mean_source][slit_source];"
            f"[mean_source]scale=1:{TIMELINE_HEIGHT}:flags=area[mean];"
            f"[slit_source]crop=1:ih:floor(iw/2)-1:0,"
            f"scale=1:{TIMELINE_HEIGHT}:flags=area[slit];"
            f"[mean][slit]hstack=inputs=2[combined]"
        ),
        "-map", "[combined]",
        "-fps_mode", "passthrough",
        "-pix_fmt", "bgr24",
        "-f", "rawvideo",
        "pipe:1",
    ]
    result = subprocess.run(command, check=True, capture_output=True)
    bytes_per_frame = 2 * TIMELINE_HEIGHT * 3
    if len(result.stdout) % bytes_per_frame:
        raise RuntimeError("FFmpeg returned an incomplete timeline frame")

    frame_count = len(result.stdout) // bytes_per_frame
    if not frame_count:
        raise RuntimeError(f"No frames decoded from video: {video_path}")

    decoded = np.frombuffer(result.stdout, dtype=np.uint8).reshape(
        frame_count, TIMELINE_HEIGHT, 2, 3
    )
    complete_timelines = {
        "mean": decoded[:, :, 0, :].transpose(1, 0, 2),
        "slitscan": decoded[:, :, 1, :].transpose(1, 0, 2),
    }
    tile_count = 0
    for variant, complete_timeline in complete_timelines.items():
        tiles = [
            complete_timeline[:, start:start + frames_per_tile]
            for start in range(0, frame_count, frames_per_tile)
        ]
        tile_count = len(tiles)
        for index, tile in enumerate(tiles, start=1):
            tile_path = output_dir / f"timeline_{TIMELINE_HEIGHT}_{index:02d}_{variant}.png"
            if not cv2.imwrite(str(tile_path), tile):
                raise RuntimeError(f"Could not write {tile_path}")

        interpolation = (
            cv2.INTER_AREA
            if complete_timeline.shape[1] > OVERVIEW_WIDTH
            else cv2.INTER_LINEAR
        )
        overview = cv2.resize(
            complete_timeline,
            (OVERVIEW_WIDTH, OVERVIEW_HEIGHT),
            interpolation=interpolation,
        )
        overview_path = output_dir / f"timeline_16_{variant}.png"
        if not cv2.imwrite(str(overview_path), overview):
            raise RuntimeError(f"Could not write {overview_path}")

    metadata = {
        "video": str(video_path),
        "frame_rate": str(info["frame_rate"]),
        "frame_count": frame_count,
        "frames_per_tile": frames_per_tile,
        "tile_count": tile_count,
        "tile_height": TIMELINE_HEIGHT,
        "variants": list(TIMELINE_VARIANTS),
        "overview_width": OVERVIEW_WIDTH,
        "overview_height": OVERVIEW_HEIGHT,
    }
    (output_dir / "timeline.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Generated {tile_count} tile(s) for each of {len(TIMELINE_VARIANTS)} timeline variants from {frame_count} frames")
    print(output_dir)
    return output_dir


# -----------------------------------------------------------------------------
# Cut detection
# -----------------------------------------------------------------------------

def detect_cuts(filename):
    video_path = Path(filename).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    output_dir = CUTS_DIR / video_path.stem
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    command = [
        sys.executable, "-m", "scenedetect",
        "-i", str(video_path),
        "--backend", "pyav",
        "-o", str(output_dir),
        "--stats", str(output_dir / "stats.csv"),
        "detect-adaptive",
        "list-scenes",
        "-f", "cuts.csv",
    ]
    subprocess.run(command, check=True)
    print(output_dir)
    return output_dir


# -----------------------------------------------------------------------------
# Local scene editor
# -----------------------------------------------------------------------------

def load_detected_scenes(video_path):
    cuts_path = CUTS_DIR / video_path.stem / "cuts.csv"
    if not cuts_path.is_file():
        raise FileNotFoundError(f"Run cut detection first: {cuts_path}")

    lines = cuts_path.read_text(encoding="utf-8-sig").splitlines()
    if len(lines) < 3:
        raise ValueError(f"Invalid cut list: {cuts_path}")

    scenes = []
    for row in csv.DictReader(lines[1:]):
        scenes.append({
            "start_frame": int(row["Start Frame"]) - 1,
            "end_frame": int(row["End Frame"]),
            "title": "Untitled",
            "location": "t.b.d.",
            "keyframe_frame": int(row["Start Frame"]) - 1,
        })
    if not scenes:
        raise ValueError(f"No scenes found in {cuts_path}")
    return scenes


def build_editor_project(video_path):
    info = probe_video(video_path)
    timeline_dir = TIMELINES_DIR / video_path.stem
    metadata_path = timeline_dir / "timeline.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Generate timelines first: {timeline_dir}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    frame_count = metadata["frame_count"]
    frames_per_tile = metadata["frames_per_tile"]
    project_id = video_path.stem
    encoded_project_id = quote(project_id, safe="")
    timeline_variants = {}
    for variant in metadata.get("variants", []):
        overview_path = timeline_dir / f"timeline_16_{variant}.png"
        if not overview_path.is_file():
            raise FileNotFoundError(overview_path)
        tiles = []
        for index in range(1, metadata["tile_count"] + 1):
            tile_path = timeline_dir / f"timeline_{metadata['tile_height']}_{index:02d}_{variant}.png"
            if not tile_path.is_file():
                raise FileNotFoundError(tile_path)
            start = (index - 1) * frames_per_tile
            tiles.append({
                "url": f"/timeline/{encoded_project_id}/{tile_path.name}",
                "width": min(frames_per_tile, frame_count - start),
            })
        timeline_variants[variant] = {
            "overview_url": f"/timeline/{encoded_project_id}/{overview_path.name}",
            "tiles": tiles,
        }
    if set(timeline_variants) != set(TIMELINE_VARIANTS):
        raise ValueError(f"Regenerate timelines to create variants: {timeline_dir}")

    rate = info["frame_rate"]
    return {
        "project_id": project_id,
        "video_name": video_path.name,
        "video_url": f"/video/{encoded_project_id}",
        "frame_url_prefix": f"/frame/{encoded_project_id}/",
        "video_width": info["width"],
        "video_height": info["height"],
        "frame_rate_num": rate.numerator,
        "frame_rate_den": rate.denominator,
        "frame_count": frame_count,
        "timeline_height": metadata["tile_height"],
        "timeline_variants": timeline_variants,
        "output_filename": f"{video_path.stem}_scenes.json",
        "scenes": load_detected_scenes(video_path),
    }


def serve_editor(filename, port):
    video_path = Path(filename).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    projects = {}
    project_paths = {}
    candidates = [video_path]
    candidates.extend(
        path for path in sorted(video_path.parent.iterdir(), key=lambda path: path.name.lower())
        if path != video_path and path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".m4v"}
    )
    for candidate in candidates:
        if not (TIMELINES_DIR / candidate.stem / "timeline.json").is_file():
            continue
        if not (CUTS_DIR / candidate.stem / "cuts.csv").is_file():
            continue
        try:
            candidate_project = build_editor_project(candidate)
        except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError):
            if candidate == video_path:
                raise
            continue
        project_id = candidate_project["project_id"]
        if project_id in projects:
            continue
        projects[project_id] = candidate_project
        project_paths[project_id] = candidate

    initial_project_id = video_path.stem
    if initial_project_id not in projects:
        raise FileNotFoundError(f"Prepare timelines and cuts first: {video_path}")
    projects_by_name = {project["video_name"]: project for project in projects.values()}
    available_videos = sorted(projects_by_name, key=str.lower)
    editor_dir = Path(__file__).resolve().parent / "editor"

    class EditorHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def sendBytes(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def sendFile(self, path, content_type, allow_ranges=False):
            size = path.stat().st_size
            start = 0
            end = size - 1
            status = 200
            range_header = self.headers.get("Range") if allow_ranges else None
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if not match:
                    self.send_error(416)
                    return
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = int(match.group(2))
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206

            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            if allow_ranges:
                self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()

            try:
                remaining = end - start + 1
                with path.open("rb") as source:
                    source.seek(start)
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            parsed_url = urlparse(self.path)
            request_path = unquote(parsed_url.path)
            if request_path == "/":
                self.sendBytes(EDITOR_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if request_path == "/api/project":
                requested_name = parse_qs(parsed_url.query).get("video", [None])[0]
                selected_project = projects_by_name.get(requested_name) if requested_name else projects[initial_project_id]
                if selected_project is None:
                    self.send_error(404, "Unknown video")
                    return
                payload = {**selected_project, "available_videos": available_videos}
                self.sendBytes(
                    json.dumps(payload).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
                return
            video_match = re.fullmatch(r"/video/([^/]+)", request_path)
            if video_match:
                selected_path = project_paths.get(video_match.group(1))
                if selected_path is None:
                    self.send_error(404)
                    return
                self.sendFile(selected_path, "video/mp4", allow_ranges=True)
                return
            if request_path.startswith("/editor/"):
                name = Path(request_path).name
                if name in {"player.png", "keyframe.png", "cut.png"}:
                    asset_path = editor_dir / name
                    if asset_path.is_file():
                        self.sendFile(asset_path, "image/png")
                        return
                self.send_error(404)
                return
            timeline_match = re.fullmatch(r"/timeline/([^/]+)/(timeline_[^/]+\.png)", request_path)
            if timeline_match:
                project_id, name = timeline_match.groups()
                timeline_path = TIMELINES_DIR / project_id / name
                if timeline_path.is_file() and name.startswith("timeline_") and name.endswith(".png"):
                    self.sendFile(timeline_path, "image/png")
                else:
                    self.send_error(404)
                return
            match = re.fullmatch(r"/frame/([^/]+)/(\d+)\.png", request_path)
            if match:
                project_id, frame_text = match.groups()
                selected_project = projects.get(project_id)
                selected_path = project_paths.get(project_id)
                if selected_project is None or selected_path is None:
                    self.send_error(404)
                    return
                frame = int(frame_text)
                if frame < 0 or frame >= selected_project["frame_count"]:
                    self.send_error(404)
                    return
                thumbnail_dir = SCENES_DIR / project_id / "thumbnails"
                thumbnail_dir.mkdir(parents=True, exist_ok=True)
                thumbnail_path = thumbnail_dir / f"frame_{frame:08d}.png"
                if not thumbnail_path.is_file():
                    temporary_path = thumbnail_path.with_suffix(".tmp.png")
                    command = [
                        "ffmpeg",
                        "-v", "error",
                        "-i", str(selected_path),
                        "-vf", f"select=eq(n\\,{frame})",
                        "-frames:v", "1",
                        "-fps_mode", "vfr",
                        "-y", str(temporary_path),
                    ]
                    try:
                        subprocess.run(command, check=True)
                        temporary_path.replace(thumbnail_path)
                    except subprocess.CalledProcessError:
                        temporary_path.unlink(missing_ok=True)
                        self.send_error(500, "Could not extract frame")
                        return
                self.sendFile(thumbnail_path, "image/png")
                return
            if request_path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", port), EditorHandler)
    print(f"Scene editor: http://127.0.0.1:{port}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scene-by-scene trailer editor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    timelines_parser = subparsers.add_parser(
        "timelines", help="Generate detailed and overview timeline images"
    )
    timelines_parser.add_argument("video")

    cuts_parser = subparsers.add_parser(
        "cuts", help="Run PySceneDetect and save its raw output"
    )
    cuts_parser.add_argument("video")

    editor_parser = subparsers.add_parser(
        "editor", help="Open the local scene editor"
    )
    editor_parser.add_argument("video")
    editor_parser.add_argument("--port", type=int, default=8028)

    args = parser.parse_args()
    if args.command == "timelines":
        generate_timelines(args.video)
    elif args.command == "cuts":
        detect_cuts(args.video)
    elif args.command == "editor":
        serve_editor(args.video, args.port)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
