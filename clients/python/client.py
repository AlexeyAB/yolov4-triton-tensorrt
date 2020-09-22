#!/usr/bin/env python

import argparse
import numpy as np
import sys
import cv2

import tritonclient.grpc as grpcclient
from tritonclient.utils import InferenceServerException

from processing import preprocess, postprocess
from render import render_box, render_filled_box, get_text_size, render_text, RAND_COLORS
from labels import COCOLabels

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode',
                        choices=['dummy', 'image', 'video'],
                        default='dummy',
                        help='Run mode. \'dummy\' will send an emtpy buffer to the server to test if inference works. \'image\' will process an image. \'video\' will process a video.')
    parser.add_argument('input',
                        type=str,
                        nargs='?',
                        help='Input file to load from in image or video mode')
    parser.add_argument('-m',
                        '--model',
                        type=str,
                        required=False,
                        default='yolov4',
                        help='Inference model name. Default is yolov4.')
    parser.add_argument('-u',
                        '--url',
                        type=str,
                        required=False,
                        default='localhost:8001',
                        help='Inference server URL. Default is localhost:8001.')
    parser.add_argument('-i',
                        '--model-info',
                        action="store_true",
                        required=False,
                        default=False,
                        help='Print model status, configuration and statistics')
    parser.add_argument('-v',
                        '--verbose',
                        action="store_true",
                        required=False,
                        default=False,
                        help='Enable verbose client output')
    parser.add_argument('-t',
                        '--client-timeout',
                        type=float,
                        required=False,
                        default=None,
                        help='Client timeout in seconds. Default is None.')
    parser.add_argument('-s',
                        '--ssl',
                        action="store_true",
                        required=False,
                        default=False,
                        help='Enable SSL encrypted channel to the server')
    parser.add_argument('-r',
                        '--root-certificates',
                        type=str,
                        required=False,
                        default=None,
                        help='File holding PEM-encoded root certificates. Default is None.')
    parser.add_argument('-p',
                        '--private-key',
                        type=str,
                        required=False,
                        default=None,
                        help='File holding PEM-encoded private key. Default is None.')
    parser.add_argument('-x',
                        '--certificate-chain',
                        type=str,
                        required=False,
                        default=None,
                        help='File holding PEM-encoded certicate chain. Default is None.')

    FLAGS = parser.parse_args()

    # Create server context
    try:
        triton_client = grpcclient.InferenceServerClient(
            url=FLAGS.url,
            verbose=FLAGS.verbose,
            ssl=FLAGS.ssl,
            root_certificates=FLAGS.root_certificates,
            private_key=FLAGS.private_key,
            certificate_chain=FLAGS.certificate_chain)
    except Exception as e:
        print("context creation failed: " + str(e))
        sys.exit()

    # Health check
    if not triton_client.is_server_live():
        print("FAILED : is_server_live")
        sys.exit(1)

    if not triton_client.is_server_ready():
        print("FAILED : is_server_ready")
        sys.exit(1)
    
    if not triton_client.is_model_ready(FLAGS.model):
        print("FAILED : is_model_ready")
        sys.exit(1)

    if FLAGS.model_info:
        # Model metadata
        try:
            metadata = triton_client.get_model_metadata(FLAGS.model)
            print(metadata)
        except InferenceServerException as ex:
            if "Request for unknown model" not in ex.message():
                print("FAILED : get_model_metadata")
                print("Got: {}".format(ex.message()))
                sys.exit(1)
            else:
                print("FAILED : get_model_metadata")
                sys.exit(1)

        # Model configuration
        try:
            config = triton_client.get_model_config(FLAGS.model)
            if not (config.config.name == FLAGS.model):
                print("FAILED: get_model_config")
                sys.exit(1)
            print(config)
        except InferenceServerException as ex:
            print("FAILED : get_model_config")
            print("Got: {}".format(ex.message()))
            sys.exit(1)

    # DUMMY MODE
    if FLAGS.mode == 'dummy':
        print("Running in 'dummy' mode")
        print("Creating emtpy buffer filled with zeros...")
        inputs = []
        outputs = []
        inputs.append(grpcclient.InferInput('data', [1, 3, 608, 608], "FP32"))
        inputs[0].set_data_from_numpy(np.zeros(shape=(1, 3, 608, 608), dtype=np.float32))
        outputs.append(grpcclient.InferRequestedOutput('prob'))

        print("Invoking inference...")
        results = triton_client.infer(model_name=FLAGS.model,
                                    inputs=inputs,
                                    outputs=outputs,
                                    client_timeout=FLAGS.client_timeout)
        if FLAGS.model_info:
            statistics = triton_client.get_inference_statistics(model_name=FLAGS.model)
            if len(statistics.model_stats) != 1:
                print("FAILED: get_inference_statistics")
                sys.exit(1)
            print(statistics)
        print("Done")

        result = results.as_numpy('prob')
        print(f"Received result buffer of size {result.shape}")
        print(f"Naive buffer sum: {np.sum(result)}")

    # IMAGE MODE
    if FLAGS.mode == 'image':
        print("Running in 'image' mode")
        if not FLAGS.input:
            print("FAILED: no input image")
            sys.exit(1)
        
        inputs = []
        outputs = []
        inputs.append(grpcclient.InferInput('data', [1, 3, 608, 608], "FP32"))
        outputs.append(grpcclient.InferRequestedOutput('prob'))

        print("Creating buffer from image file...")
        input_image = cv2.imread(str(FLAGS.input))
        if input_image is None:
            print(f"FAILED: could not load input image {str(FLAGS.input)}")
            sys.exit(1)
        input_image_buffer = preprocess(input_image)
        input_image_buffer = np.expand_dims(input_image_buffer, axis=0)
        inputs[0].set_data_from_numpy(input_image_buffer)

        print("Invoking inference...")
        results = triton_client.infer(model_name=FLAGS.model,
                                    inputs=inputs,
                                    outputs=outputs,
                                    client_timeout=FLAGS.client_timeout)
        if FLAGS.model_info:
            statistics = triton_client.get_inference_statistics(model_name=FLAGS.model)
            if len(statistics.model_stats) != 1:
                print("FAILED: get_inference_statistics")
                sys.exit(1)
            print(statistics)
        print("Done")

        result = results.as_numpy('prob')
        print(f"Received result buffer of size {result.shape}")
        print(f"Naive buffer sum: {np.sum(result)}")

        detected_objects = postprocess(result, input_image.shape[1], input_image.shape[0])
        print(f"Detected objects: {len(detected_objects)}")

        for box in detected_objects:
            input_image = render_box(input_image, box.box(), color=tuple(RAND_COLORS[box.classID % 64].tolist()))
            size = get_text_size(input_image, COCOLabels(box.classID).name, normalised_scaling=0.6)
            input_image = render_filled_box(input_image, (box.x1 - 3, box.y1 - 3, box.x1 + size[0], box.y1 + size[1]), color=(220, 220, 220))
            input_image = render_text(input_image, COCOLabels(box.classID).name, (box.x1, box.y1), color=(30, 30, 30), normalised_scaling=0.5)

        cv2.imshow('image', input_image)
        cv2.waitKey(0)
        cv2.destroyAllWindows()