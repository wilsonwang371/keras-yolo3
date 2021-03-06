#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""
Run a YOLO_v3 style detection model on test images.
"""

import argparse
import colorsys
import io
import os
import time
from timeit import default_timer as timer

import numpy as np
import tensorflow as tf
from keras import backend as K
from keras.layers import Input
from keras.models import load_model
from PIL import Image, ImageDraw, ImageFont

import cv2
from yolo3.model import tiny_yolo_body, yolo_body, yolo_eval
from yolo3.utils import letterbox_image

graph = None
quit_thread = False

class YOLO(object):
    def __init__(self, model_path, anchor_path, class_path):
        self.model_path = model_path # model path or trained weights path
        self.anchors_path = anchor_path
        self.classes_path = class_path
        self.score = 0.4
        self.iou = 0.45
        self.class_names = self._get_class()
        self.anchors = self._get_anchors()
        self.sess = K.get_session()
        self.model_image_size = (416, 416) # fixed size or (None, None), hw
        self.boxes, self.scores, self.classes = self.generate()
        self.font = None

    def _get_class(self):
        classes_path = os.path.expanduser(self.classes_path)
        with open(classes_path) as f:
            class_names = f.readlines()
        class_names = [c.strip() for c in class_names]
        return class_names

    def _get_anchors(self):
        anchors_path = os.path.expanduser(self.anchors_path)
        with open(anchors_path) as f:
            anchors = f.readline()
        anchors = [float(x) for x in anchors.split(',')]
        return np.array(anchors).reshape(-1, 2)

    def generate(self):
        model_path = os.path.expanduser(self.model_path)
        assert model_path.endswith('.h5'), 'Keras model or weights must be a .h5 file.'

        # Load model, or construct model and load weights.
        num_anchors = len(self.anchors)
        num_classes = len(self.class_names)
        is_tiny_version = num_anchors==6 # default setting
        try:
            self.yolo_model = load_model(model_path, compile=False)
        except:
            self.yolo_model = tiny_yolo_body(Input(shape=(None,None,3)), num_anchors//2, num_classes) \
                if is_tiny_version else yolo_body(Input(shape=(None,None,3)), num_anchors//3, num_classes)
            self.yolo_model.load_weights(self.model_path) # make sure model, anchors and classes match
        else:
            assert self.yolo_model.layers[-1].output_shape[-1] == \
                num_anchors/len(self.yolo_model.output) * (num_classes + 5), \
                'Mismatch between model and given anchor and class sizes'

        print('{} model, anchors, and classes loaded.'.format(model_path))

        # Generate colors for drawing bounding boxes.
        hsv_tuples = [(x / len(self.class_names), 1., 1.)
                      for x in range(len(self.class_names))]
        self.colors = list(map(lambda x: colorsys.hsv_to_rgb(*x), hsv_tuples))
        self.colors = list(
            map(lambda x: (int(x[0] * 255), int(x[1] * 255), int(x[2] * 255)),
                self.colors))
        np.random.seed(10101)  # Fixed seed for consistent colors across runs.
        np.random.shuffle(self.colors)  # Shuffle colors to decorrelate adjacent classes.
        np.random.seed(None)  # Reset seed to default.

        # Generate output tensor targets for filtered bounding boxes.
        self.input_image_shape = K.placeholder(shape=(2, ))
        boxes, scores, classes = yolo_eval(self.yolo_model.output, self.anchors,
                len(self.class_names), self.input_image_shape,
                score_threshold=self.score, iou_threshold=self.iou)
        return boxes, scores, classes

    def detect_image(self, image, verbose=True):
        if verbose:
            start = timer()

        if self.model_image_size != (None, None):
            assert self.model_image_size[0]%32 == 0, 'Multiples of 32 required'
            assert self.model_image_size[1]%32 == 0, 'Multiples of 32 required'
            boxed_image = letterbox_image(image, tuple(reversed(self.model_image_size)))
        else:
            new_image_size = (image.width - (image.width % 32),
                              image.height - (image.height % 32))
            boxed_image = letterbox_image(image, new_image_size)
        image_data = np.array(boxed_image, dtype='float32')

        if verbose:
            print(image_data.shape)
        image_data /= 255.
        image_data = np.expand_dims(image_data, 0)  # Add batch dimension.

        out_boxes, out_scores, out_classes = self.sess.run(
            [self.boxes, self.scores, self.classes],
            feed_dict={
                self.yolo_model.input: image_data,
                self.input_image_shape: [image.size[1], image.size[0]],
                K.learning_phase(): 0
            })

        if verbose:
            print('Found {} boxes for {}'.format(len(out_boxes), 'img'))

        if hasattr(self, 'font') and self.font is not None:
            font = self.font
        else:
            font = ImageFont.truetype(font='font/FiraMono-Medium.otf',
                        size=np.floor(3e-2 * image.size[1] + 0.5).astype('int32'))
            self.font = font
        thickness = (image.size[0] + image.size[1]) // 300

        for i, c in reversed(list(enumerate(out_classes))):
            predicted_class = self.class_names[c]
            box = out_boxes[i]
            score = out_scores[i]

            label = '{} {:.2f}'.format(predicted_class, score)
            draw = ImageDraw.Draw(image)
            label_size = draw.textsize(label, font)

            top, left, bottom, right = box
            top = max(0, np.floor(top + 0.5).astype('int32'))
            left = max(0, np.floor(left + 0.5).astype('int32'))
            bottom = min(image.size[1], np.floor(bottom + 0.5).astype('int32'))
            right = min(image.size[0], np.floor(right + 0.5).astype('int32'))
            print(label, (left, top), (right, bottom))

            if top - label_size[1] >= 0:
                text_origin = np.array([left, top - label_size[1]])
            else:
                text_origin = np.array([left, top + 1])

            # My kingdom for a good redistributable image drawing library.
            for i in range(thickness):
                draw.rectangle(
                    [left + i, top + i, right - i, bottom - i],
                    outline=self.colors[c])
            draw.rectangle(
                [tuple(text_origin), tuple(text_origin + label_size)],
                fill=self.colors[c])
            draw.text(text_origin, label, fill=(0, 0, 0), font=font)
            del draw

        if verbose:
            end = timer()
            print(end - start)
        return image

    def close_session(self):
        self.sess.close()


def detect_video(yolo, video_path, output_path=""):
    vid = cv2.VideoCapture(video_path)
    if not vid.isOpened():
        raise IOError("Couldn't open webcam or video")
    video_FourCC    = int(vid.get(cv2.CAP_PROP_FOURCC))
    video_fps       = vid.get(cv2.CAP_PROP_FPS)
    video_size      = (int(vid.get(cv2.CAP_PROP_FRAME_WIDTH)),
                        int(vid.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    isOutput = True if output_path != "" else False
    if isOutput:
        print("!!! TYPE:", type(output_path), type(video_FourCC), type(video_fps), type(video_size))
        out = cv2.VideoWriter(output_path, video_FourCC, video_fps, video_size)
    accum_time = 0
    curr_fps = 0
    fps = "FPS: ??"
    prev_time = timer()
    while True:
        return_value, frame = vid.read()
        image = Image.fromarray(frame)
        image = yolo.detect_image(image)
        result = np.asarray(image)
        curr_time = timer()
        exec_time = curr_time - prev_time
        prev_time = curr_time
        accum_time = accum_time + exec_time
        curr_fps = curr_fps + 1
        if accum_time > 1:
            accum_time = accum_time - 1
            fps = "FPS: " + str(curr_fps)
            curr_fps = 0
        cv2.putText(result, text=fps, org=(3, 15), fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                    fontScale=0.50, color=(255, 0, 0), thickness=2)
        cv2.namedWindow("result", cv2.WINDOW_NORMAL)
        cv2.imshow("result", result)
        if isOutput:
            out.write(result)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    yolo.close_session()


def detect_img(yolo, img_path):
    while True:
        if img_path == '':
            img = input('Input image filename:')
        else:
            img = img_path
        try:
            image = Image.open(img)
        except:
            print('Open Error! Try again!')
            continue
        else:
            r_image = yolo.detect_image(image)
            cv_r_img = np.array(r_image)
            #r_image.show()
            cv2.namedWindow('r_image', cv2.WINDOW_NORMAL)
            cv2.imshow('r_image', cv_r_img)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        if img_path != '':
            break
    yolo.close_session()


def detect_picamera(yolo):
    ''' Raspberry PI camera
    '''
    from picamera.array import PiRGBArray
    from picamera import PiCamera
    import threading
    import queue

    global graph
    global quit_thread

    graph = tf.get_default_graph()
    camera = PiCamera()
    camera.resolution = (640, 480)
    #camera.framerate = 32
    rawCapture = PiRGBArray(camera, size=(640, 480))

    cv2.namedWindow('picam', cv2.WINDOW_NORMAL)
    cam_q = queue.Queue(4)
    processed_q = queue.Queue(4)
    processThread = threading.Thread(target=detect_picamera_yolo_thread_func,
                                     args=(yolo, cam_q, processed_q))
    processThread.start()
    print('started')
    for img_frame in camera.capture_continuous(rawCapture, format='bgr', use_video_port=True):
        try:
            img = Image.fromarray(img_frame.array)
            if cam_q.full():
                time.sleep(1)
                while not cam_q.empty():
                    cam_q.get()
            cam_q.put(img)
            while not processed_q.empty():
                cv2.imshow('picam', np.array(processed_q.get()))
                cv2.waitKey(1)
            rawCapture.truncate(0)
        except KeyboardInterrupt:
            print('exiting...')
            break
    quit_thread = True
    processThread.join()
    cv2.destroyAllWindows()
    yolo.close_session()


def detect_picamera_yolo_thread_func(yolo, cam_queue, out_queue):
    global graph
    assert graph is not None
    with graph.as_default():
        while not quit_thread:
            img = cam_queue.get()
            r_image = yolo.detect_image(img, False)
            out_queue.put(r_image)


if __name__ == '__main__':
    # my command python3 ./yolo.py -m  model_data/yolov3-tiny.h5
    #                              -c model_data/coco_classes.txt
    #                              -a model_data/tiny_yolo_anchors.txt
    parser = argparse.ArgumentParser()
    parser.add_argument('-m', '--modelpath', help='model file path', default='model_data/yolo.h5')
    parser.add_argument('-a', '--anchorpath', help='anchor file path', default='model_data/yolo_anchors.txt')
    parser.add_argument('-c', '--classpath', help='class file path', default='model_data/coco_classes.txt')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('-i', '--image', help='image file path', default='')
    action.add_argument('-p', '--picam', help='fetch images from Raspberry PI camera',
                        action='store_true', default=False)
    args = parser.parse_args()
    if args.picam == True:
        detect_picamera(YOLO(args.modelpath, args.anchorpath, args.classpath))
    else:
        detect_img(YOLO(args.modelpath, args.anchorpath, args.classpath), args.image)
