#!/bin/python

import os
import sys

import cv2
import numpy as np
from optparse import OptionParser
import datetime as dt

import tensorflow.contrib.slim as slim
import tensorflow as tf
from tensorflow.python.platform import gfile

import shutil
import wget
import tarfile

# Constants
TRAIN = 0
VAL = 1
TEST = 2

COLORS = np.array([[0, 0, 0], [0, 128, 0], [0, 0, 128], [192, 224, 224]]) # RGB
LABELS = np.array([0, 1, 1, 2]) # Give high weight to the boundary

# Command line options
parser = OptionParser()

# General settings
parser.add_option("-t", "--trainModel", action="store_true", dest="trainModel", default=False, help="Train model")
parser.add_option("-c", "--testModel", action="store_true", dest="testModel", default=False, help="Test model")
parser.add_option("-d", "--debug", action="store_true", dest="debug", default=False, help="Enable debugging model - high verbosity")
parser.add_option("--tensorboardVisualization", action="store_true", dest="tensorboardVisualization", default=False, help="Enable tensorboard visualization")

# Input Reader Params
parser.add_option("--trainFileName", action="store", type="string", dest="trainFileName", default="./data/train.csv", help="File containing the training file names")
parser.add_option("--valFileName", action="store", type="string", dest="valFileName", default="./data/val.csv", help="File containing the validation file names")
parser.add_option("--testFileName", action="store", type="string", dest="testFileName", default="./data/test.csv", help="File containing the test file names")
parser.add_option("--statsFileName", action="store", type="string", dest="statsFileName", default="stats.txt", help="Image database statistics (mean, var)")
parser.add_option("--maxImageSize", action="store", type="int", dest="maxImageSize", default=2048, help="Maximum size of the larger dimension while preserving aspect ratio")
parser.add_option("--imageChannels", action="store", type="int", dest="imageChannels", default=3, help="Number of channels in image for feeding into the network")
parser.add_option("--shufflePerBatch", action="store_true", dest="shufflePerBatch", default=False, help="Shuffle input for every batch")
parser.add_option("--mapLabelsFromRGB", action="store_true", dest="mapLabelsFromRGB", default=False, help="Map labels from RGB to integers (if data is in form [H, W, 3])")
parser.add_option("--useSparseLabels", action="store_true", dest="useSparseLabels", default=False, help="Use sparse labels (Mask shape: [H, W, 1] instead of [H, W, C] where C is the number of classes)")
parser.add_option("--boundaryWeight", action="store", type="float", dest="boundaryWeight", default=10.0, help="Weight to be given to the boundary for computing the total loss")
parser.add_option("--numParallelLoaders", action="store", type="int", dest="numParallelLoaders", default=8, help="Number of parallel loaders to be used for data loading")

# Trainer Params
parser.add_option("--learningRate", action="store", type="float", dest="learningRate", default=1e-4, help="Learning rate")
parser.add_option("--weightDecayLambda", action="store", type="float", dest="weightDecayLambda", default=5e-5, help="Weight Decay Lambda")
parser.add_option("--trainingEpochs", action="store", type="int", dest="trainingEpochs", default=5, help="Training epochs")
parser.add_option("--batchSize", action="store", type="int", dest="batchSize", default=1, help="Batch size")
parser.add_option("--displayStep", action="store", type="int", dest="displayStep", default=5, help="Progress display step")
parser.add_option("--saveStep", action="store", type="int", dest="saveStep", default=1000, help="Progress save step")
parser.add_option("--evaluateStep", action="store", type="int", dest="evaluateStep", default=100000, help="Progress evaluation step")
parser.add_option("--evaluateStepDontSaveImages", action="store_true", dest="evaluateStepDontSaveImages", default=False, help="Don't save images on evaluate step")
parser.add_option("--trainImagesOutputDirectory", action="store", type="string", dest="trainImagesOutputDirectory", default="./outputImages_train", help="Directory for saving output images for train set")
parser.add_option("--valImagesOutputDirectory", action="store", type="string", dest="valImagesOutputDirectory", default="./outputImages_val", help="Directory for saving output images for validation set")
parser.add_option("--testImagesOutputDirectory", action="store", type="string", dest="testImagesOutputDirectory", default="./outputImages_test", help="Directory for saving output images for test set")

# Directories
parser.add_option("--logsDir", action="store", type="string", dest="logsDir", default="./logs", help="Directory for saving logs")
parser.add_option("--pretrainedModelsDir", action="store", type="string", dest="pretrainedModelsDir", default="./pretrained/", help="Directory containing the pretrained models")
parser.add_option("--outputModelDir", action="store", type="string", dest="outputModelDir", default="./output/", help="Directory for saving the model")
parser.add_option("--outputModelName", action="store", type="string", dest="outputModelName", default="Model", help="Name to be used for saving the model")

# Network Params
parser.add_option("-m", "--modelName", action="store", dest="modelName", default="NASNet", choices=["NASNet", "IncResV2"], help="Name of the model to be used")
parser.add_option("-s", "--startTrainingFromScratch", action="store_true", dest="startTrainingFromScratch", default=False, help="Start training from scratch")
parser.add_option("--numClasses", action="store", type="int", dest="numClasses", default=3, help="Number of classes")
parser.add_option("--ignoreLabel", action="store", type="int", dest="ignoreLabel", default=255, help="Label to ignore for loss computation")
parser.add_option("--useSkipConnections", action="store_true", dest="useSkipConnections", default=False, help="Use skip connections or not")
parser.add_option("--useCRFPostProcessing", action="store_true", dest="useCRFPostProcessing", default=False, help="Use CRF based post-processing")

# Parse command line options
(options, args) = parser.parse_args()

# Verification
assert options.batchSize == 1, "Error: Only batch size of 1 is supported due to aspect aware scaling!"
try:
	import pydensecrf.densecrf as dcrf
except:
	# print("Error: Failed to import pydensecrf! CRF post-processing will not work.")
	assert not options.useCRFPostProcessing, "Error: Failed to import pydensecrf!"

options.outputModelDir = os.path.join(options.outputModelDir, "trained-" + options.modelName)
options.outputModelName = options.outputModelName + "_" + options.modelName

options.trainImagesOutputDirectory = os.path.join(options.outputModelDir, options.trainImagesOutputDirectory)
options.valImagesOutputDirectory = os.path.join(options.outputModelDir, options.valImagesOutputDirectory)
options.testImagesOutputDirectory = os.path.join(options.outputModelDir, options.testImagesOutputDirectory)

print (options)

# Check if the pretrained directory exists
if not os.path.exists(options.pretrainedModelsDir):
	print ("Warning: Pretrained models directory not found!")
	os.makedirs(options.pretrainedModelsDir)
	assert os.path.exists(options.pretrainedModelsDir)

# Clone the repository if not already existent
if not os.path.exists(os.path.join(options.pretrainedModelsDir, "models/research/slim")):
	print ("Cloning TensorFlow models repository")
	import git # gitpython

	class Progress(git.remote.RemoteProgress):
		def update(self, op_code, cur_count, max_count=None, message=''):
			print (self._cur_line)

	git.Repo.clone_from("https://github.com/tensorflow/models.git", os.path.join(options.pretrainedModelsDir, "models"), progress=Progress())
	print ("Repository sucessfully cloned!")

# Add the path to the tensorflow models repository
sys.path.append(os.path.join(options.pretrainedModelsDir, "models/research/slim"))
sys.path.append(os.path.join(options.pretrainedModelsDir, "models/research/slim/nets"))

import inception_resnet_v2
import resnet_v1
import nasnet.nasnet as nasnet

# Import FCN Model
if options.modelName == "NASNet":
	print ("Downloading pretrained NASNet model")
	nasCheckpointFile = checkpointFileName = os.path.join(options.pretrainedModelsDir, options.modelName, 'model.ckpt')
	if not os.path.isfile(nasCheckpointFile + '.index'):
		# Download file from the link
		url = 'https://storage.googleapis.com/download.tensorflow.org/models/nasnet-a_large_04_10_2017.tar.gz'
		fileName = wget.download(url, options.pretrainedModelsDir)
		print ("File downloaded: %s" % fileName)

		# Extract the tar file
		tar = tarfile.open(fileName)
		tar.extractall(path=os.path.join(options.pretrainedModelsDir, options.modelName))
		tar.close()

	# Update image sizes
	# options.imageHeight = options.imageWidth = 331

elif options.modelName == "IncResV2":
	print ("Downloading pretrained Inception ResNet v2 model")
	incResV2CheckpointFile = checkpointFileName = os.path.join(options.pretrainedModelsDir, options.modelName, 'inception_resnet_v2_2016_08_30.ckpt')
	if not os.path.isfile(incResV2CheckpointFile):
		# Download file from the link
		url = 'http://download.tensorflow.org/models/inception_resnet_v2_2016_08_30.tar.gz'
		fileName = wget.download(url, options.pretrainedModelsDir)
		print ("File downloaded: %s" % fileName)

		# Extract the tar file
		tar = tarfile.open(fileName)
		tar.extractall(path=os.path.join(options.pretrainedModelsDir, options.modelName))
		tar.close()

	# Update image sizes
	# options.imageHeight = options.imageWidth = 299

else:
	print ("Error: Model not found!")
	exit (-1)

# Reads an image from a file, decodes it into a dense tensor
def parseFunction(imgFileName, gtFileName):
	# TODO: Replace with decode_image (decode_image doesn't return shape)
	# Load the original image
	imageString = tf.read_file(imgFileName)
	img = tf.image.decode_jpeg(imageString)
	img = tf.image.resize_images(img, [options.maxImageSize, options.maxImageSize], preserve_aspect_ratio=True)
	img.set_shape([None, None, options.imageChannels])
	img = tf.cast(img, tf.float32) # Convert to float tensor

	# Load the segmentation mask
	imageString = tf.read_file(gtFileName)
	mask = tf.image.decode_png(imageString)

	if options.mapLabelsFromRGB:
		assert False # Not working at this point
		# TODO: Optimize this mapping
		if options.useSparseLabels:
			# raise NotImplementedError
			maskNew = tf.zeros(shape=[tf.shape(mask)[0], tf.shape(mask)[1]])
			for idx, color in enumerate(colors):
				maskNew = tf.cond(tf.reduce_all(tf.equal(mask, color), axis=-1), lambda: maskNew, lambda: maskNew)
				# maskNew = tf.cond(tf.equal(mask, color), lambda: labels[idx], lambda: maskNew)

		else:
			semanticMap = []
			for color in colors:
				classMap = tf.reduce_all(tf.equal(mask, color), axis=-1)
				semanticMap.append(classMap)
			mask = tf.to_float(tf.stack(semanticMap, axis=-1))

	mask = tf.image.resize_images(mask, [options.maxImageSize, options.maxImageSize], method=tf.image.ResizeMethod.NEAREST_NEIGHBOR, preserve_aspect_ratio=True)
	mask = tf.cast(mask, tf.int32) # Convert to float tensor

	return imgFileName, img, mask

def dataAugmentationFunction(imgFileName, img, mask):
	with tf.name_scope('flipLR'):
		randomVar = tf.random_uniform(maxval=2, dtype=tf.int32, shape=[]) # Random variable: two possible outcomes (0 or 1)
		img = tf.cond(pred=tf.equal(randomVar, 0), true_fn=lambda: tf.image.flip_left_right(img), false_fn=lambda: img)
		mask = tf.cond(pred=tf.equal(randomVar, 0), true_fn=lambda: tf.image.flip_left_right(mask), false_fn=lambda: mask)

	with tf.name_scope('flipUD'):
		randomVar = tf.random_uniform(maxval=2, dtype=tf.int32, shape=[]) # Random variable: two possible outcomes (0 or 1)
		img = tf.cond(pred=tf.equal(randomVar, 0), true_fn=lambda: tf.image.flip_up_down(img), false_fn=lambda: img)
		mask = tf.cond(pred=tf.equal(randomVar, 0), true_fn=lambda: tf.image.flip_up_down(mask), false_fn=lambda: mask)

	img = tf.image.random_brightness(img, max_delta=32.0 / 255.0)
	img = tf.image.random_saturation(img, lower=0.5, upper=1.5)

	# Make sure the image is still in [0, 255]
	img = tf.clip_by_value(img, 0.0, 255.0)

	return imgFileName, img, mask

def loadDataset(currentDataFile, dataAugmentation=False):
	print ("Loading data from file: %s" % (currentDataFile))
	dataClasses = {}
	with open(currentDataFile) as f:
		imageFileNames = f.readlines()
		originalImageNames = []
		maskImageNames = []
		for imName in imageFileNames:
			imName = imName.strip().split(',')

			originalImageNames.append(imName[0])
			maskImageNames.append(imName[1])

		originalImageNames = tf.constant(originalImageNames)
		maskImageNames = tf.constant(maskImageNames)

	numFiles = len(imageFileNames)
	print ("Dataset loaded")
	print ("Number of files found: %d" % (numFiles))

	dataset = tf.data.Dataset.from_tensor_slices((originalImageNames, maskImageNames))
	dataset = dataset.map(parseFunction, num_parallel_calls=options.numParallelLoaders)

	# Data augmentation
	if dataAugmentation:
		dataset = dataset.map(dataAugmentationFunction, num_parallel_calls=options.numParallelLoaders)

	# Data shuffling
	if options.shufflePerBatch:
		dataset = dataset.shuffle(buffer_size=numFiles)

	dataset = dataset.batch(options.batchSize)

	return dataset

def writeMaskToImage(img, mask, directory, fileName, append='', overlay=True):
	fileName = fileName[0].decode("utf-8") 
	_, fileName = os.path.split(fileName) # Crop the complete path name
	img = img[0]
	mask = mask[0]
	fileNameRoot, fileNameExt = os.path.splitext(fileName)
	outputFileName = os.path.join(directory, fileNameRoot + append + fileNameExt)
	if options.debug:
		print ("Saving predicted segmentation mask:", outputFileName)

	rgbMask = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.float32)
	for color, label in zip(COLORS, LABELS):
		binaryMap = mask[:, :, 0] == label
		rgbMask[binaryMap, 0] = color[0]
		rgbMask[binaryMap, 1] = color[1]
		rgbMask[binaryMap, 2] = color[2]

	if overlay:
		rgbMask = np.uint8(cv2.addWeighted(img.astype(np.float32), 0.5, rgbMask, 0.5, 0.0))

	# Write the resulting image to file
	cv2.imwrite(outputFileName, rgbMask)

# TODO: Add skip connections
# Performs the upsampling of the given images
def attachDecoder(net, endPoints, inputShape, activation=tf.nn.relu, numFilters=256, filterSize=(3, 3), strides=(2, 2), padding='same'):
	with tf.name_scope('Decoder'), tf.variable_scope('Decoder'):
		out = tf.layers.conv2d_transpose(activation(net), numFilters, filterSize, strides=strides, padding='valid')
		if options.useSkipConnections:
			endPointName = 'Mixed_6a'
			endPoint = endPoints[endPointName]
			encShape = tf.shape(endPoint)
			out = tf.image.resize_bilinear(out, [encShape[1], encShape[2]], align_corners=True) + tf.layers.conv2d(endPoint, numFilters, (1, 1), strides=(1, 1), padding='same')

		out = tf.layers.conv2d(activation(out), numFilters, filterSize, strides=(1, 1), padding=padding)

		out = tf.layers.conv2d_transpose(activation(out), numFilters, filterSize, strides=strides, padding='valid')
		if options.useSkipConnections:
			endPointName = 'MaxPool_5a_3x3'
			endPoint = endPoints[endPointName]
			encShape = tf.shape(endPoint)
			out = tf.image.resize_bilinear(out, [encShape[1], encShape[2]], align_corners=True) + tf.layers.conv2d(endPoint, numFilters, (1, 1), strides=(1, 1), padding='same')

		out = tf.layers.conv2d(activation(out), numFilters, filterSize, strides=(1, 1), padding=padding)

		out = tf.layers.conv2d_transpose(activation(out), numFilters, filterSize, strides=strides, padding='valid')
		if options.useSkipConnections:
			endPointName = 'Conv2d_4a_3x3'
			endPoint = endPoints[endPointName]
			encShape = tf.shape(endPoint)
			out = tf.image.resize_bilinear(out, [encShape[1], encShape[2]], align_corners=True) + tf.layers.conv2d(endPoint, numFilters, (1, 1), strides=(1, 1), padding='same')

		out = tf.layers.conv2d(activation(out), numFilters, filterSize, strides=(1, 1), padding=padding)

		out = tf.layers.conv2d_transpose(activation(out), numFilters, filterSize, strides=strides, padding='valid')
		if options.useSkipConnections:
			# out = out + tf.layers.conv2d(endPoints['Conv2d_2b_3x3'], numFilters, (1, 1), strides=(1, 1), padding='same')
			endPointName = 'Conv2d_2b_3x3'
			endPoint = endPoints[endPointName]
			encShape = tf.shape(endPoint)
			out = tf.image.resize_bilinear(out, [encShape[1], encShape[2]], align_corners=True) + tf.layers.conv2d(endPoint, numFilters, (1, 1), strides=(1, 1), padding='same')

		out = tf.layers.conv2d(activation(out), numFilters, filterSize, strides=(1, 1), padding=padding)

		# Match dimensions (convolutions with 'valid' padding reducing the dimensions)
		out = tf.image.resize_bilinear(out, [inputShape[1], inputShape[2]], align_corners=True) # TODO: Is it useful or it doesn't matter?
		out = tf.layers.conv2d(activation(out), numFilters, filterSize, strides=(1, 1), padding='same')

		out = tf.layers.conv2d(activation(out), options.numClasses, filterSize, strides=(1, 1), padding=padding) # Obtain per pixel predictions
	return out

# Create dataset objects
trainDataset = loadDataset(options.trainFileName, dataAugmentation=True)
trainIterator = trainDataset.make_initializable_iterator()

valDataset = loadDataset(options.valFileName)
valIterator = valDataset.make_initializable_iterator()

testDataset = loadDataset(options.testFileName)
testIterator = testDataset.make_initializable_iterator()

# globalStep = tf.train.get_or_create_global_step() # To be used with Optimizer

# Data placeholders
datasetSelectionPlaceholder = tf.placeholder(dtype=tf.int32, shape=(), name='DatasetSelectionPlaceholder')
inputBatchImageNames, inputBatchImages, inputBatchMasks = tf.cond(tf.equal(datasetSelectionPlaceholder, TRAIN), lambda: trainIterator.get_next(), 
															lambda: tf.cond(tf.equal(datasetSelectionPlaceholder, VAL), lambda: valIterator.get_next(), lambda: testIterator.get_next()))
print ("Data shape: %s | Mask shape: %s" % (str(inputBatchImages.get_shape()), str(inputBatchMasks.get_shape())))

# if options.trainModel:
with tf.name_scope('Model'):
	# Data placeholders
	# inputBatchImagesPlaceholder = tf.placeholder(dtype=tf.float32, shape=[None, None, None, options.imageChannels], name="inputBatchImages")

	# Scaling only for NASNet and IncResV2
	scaledInputBatchImages = tf.scalar_mul((1.0 / 255.0), inputBatchImages)
	scaledInputBatchImages = tf.subtract(scaledInputBatchImages, 0.5)
	scaledInputBatchImages = tf.multiply(scaledInputBatchImages, 2.0)

	# Create model
	if options.modelName == "NASNet":
		arg_scope = nasnet.nasnet_large_arg_scope()
		with slim.arg_scope(arg_scope):
			logits, endPoints = nasnet.build_nasnet_large(scaledInputBatchImages, is_training=False, num_classes=options.numClasses)

	elif options.modelName == "IncResV2":
		arg_scope = inception_resnet_v2.inception_resnet_v2_arg_scope()
		with slim.arg_scope(arg_scope):
			# logits, endPoints = inception_resnet_v2.inception_resnet_v2(scaledInputBatchImages, is_training=False)
			with tf.variable_scope('InceptionResnetV2', 'InceptionResnetV2', [scaledInputBatchImages], reuse=None) as scope:
				with slim.arg_scope([slim.batch_norm, slim.dropout], is_training=False):
				  net, endPoints = inception_resnet_v2.inception_resnet_v2_base(scaledInputBatchImages, scope=scope, activation_fn=tf.nn.relu)

		variablesToRestore = slim.get_variables_to_restore(include=["InceptionResnetV2"])

	else:
		print ("Error: Model not found!")
		exit (-1)

# TODO: Attach the decoder to the encoder
print (endPoints.keys())
if options.useSkipConnections:
	print ("Adding skip connections from the encoder to the decoder!")
predictedLogits = attachDecoder(net, endPoints, tf.shape(scaledInputBatchImages))
predictedMask = tf.expand_dims(tf.argmax(predictedLogits, axis=-1), -1, name="predictedMasks")

if options.tensorboardVisualization:
	tf.summary.image('Original Image', inputBatchImages, max_outputs=3)
	tf.summary.image('Desired Mask', tf.to_float(inputBatchMasks), max_outputs=3)
	tf.summary.image('Predicted Mask', tf.to_float(predictedMask), max_outputs=3)

with tf.name_scope('Loss'):
	# Reshape 4D tensors to 2D, each row represents a pixel, each column a class
	predictedMaskFlattened = tf.reshape(predictedLogits, (-1, tf.shape(predictedLogits)[1] * tf.shape(predictedLogits)[2], options.numClasses), name="fcnLogits")
	inputMaskFlattened = tf.reshape(inputBatchMasks, (-1, tf.shape(inputBatchMasks)[1] * tf.shape(inputBatchMasks)[2]))
	# inputMaskFlattened = tf.layers.flatten(inputBatchMasks)

	# Define loss
	weights = tf.cast(inputMaskFlattened != options.ignoreLabel, dtype=tf.float32)
	weights = tf.cond(pred=tf.equal(weights, 2), true_fn=lambda: options.boundaryWeight, false_fn=lambda: weights)
	crossEntropyLoss = tf.losses.sparse_softmax_cross_entropy(labels=inputMaskFlattened, logits=predictedMaskFlattened, weights=weights)
	regLoss = options.weightDecayLambda * tf.reduce_sum(tf.losses.get_regularization_losses())
	loss = tf.add(crossEntropyLoss, regLoss, name="totalLoss")

with tf.name_scope('Optimizer'):
	# Define Optimizer
	optimizer = tf.train.AdamOptimizer(learning_rate=options.learningRate)

	# Op to calculate every variable gradient
	gradients = tf.gradients(loss, tf.trainable_variables())
	gradients = list(zip(gradients, tf.trainable_variables()))
	# Op to update all variables according to their gradient
	applyGradients = optimizer.apply_gradients(grads_and_vars=gradients)

# Initializing the variables
init = tf.global_variables_initializer()
init_local = tf.local_variables_initializer()

if options.tensorboardVisualization:
	# Create a summary to monitor cost tensor
	tf.summary.scalar("reg_loss", regLoss)
	tf.summary.scalar("cross_entropy", crossEntropyLoss)
	tf.summary.scalar("total_loss", loss)

	# Create summaries to visualize weights
	for var in tf.trainable_variables():
		tf.summary.histogram(var.name, var)
	# Summarize all gradients
	for grad, var in gradients:
		if grad is not None:
			tf.summary.histogram(var.name + '/gradient', grad)

	# Merge all summaries into a single op
	mergedSummaryOp = tf.summary.merge_all()

# 'Saver' op to save and restore all the variables
saver = tf.train.Saver()

# GPU config
config = tf.ConfigProto()
config.gpu_options.allow_growth=True

# Train model
if options.trainModel:
	with tf.Session(config=config) as sess:
		# Initialize all variables
		sess.run(init)
		sess.run(init_local)

		if options.startTrainingFromScratch:
			print ("Removing previous checkpoints and logs")
			if os.path.exists(options.logsDir): 
				shutil.rmtree(options.logsDir)
			if os.path.exists(options.trainImagesOutputDirectory): 
				shutil.rmtree(options.trainImagesOutputDirectory)
			if os.path.exists(options.valImagesOutputDirectory): 
				shutil.rmtree(options.valImagesOutputDirectory)
			if os.path.exists(options.testImagesOutputDirectory): 
				shutil.rmtree(options.testImagesOutputDirectory)
			if os.path.exists(options.outputModelDir): 
				shutil.rmtree(options.outputModelDir)
			
			os.makedirs(options.outputModelDir)
			os.makedirs(options.trainImagesOutputDirectory)
			os.makedirs(options.valImagesOutputDirectory)
			os.makedirs(options.testImagesOutputDirectory)

			# Load the pre-trained Inception ResNet v2 model
			restorer = tf.train.Saver(variablesToRestore)
			restorer.restore(sess, incResV2CheckpointFile)

		# Restore checkpoint
		else:
			print ("Restoring from checkpoint")
			saver = tf.train.import_meta_graph(os.path.join(options.outputModelDir, options.outputModelName + ".meta"))
			saver.restore(sess, os.path.join(options.outputModelDir, options.outputModelName))

		if options.tensorboardVisualization:
			# Op for writing logs to Tensorboard
			summaryWriter = tf.summary.FileWriter(options.logsDir, graph=tf.get_default_graph())

		print ("Starting network training")
		globalStep = 0

		# Keep training until reach max iterations
		for epoch in range(options.trainingEpochs):
			# Initialize the dataset iterators
			sess.run(trainIterator.initializer)
			
			try:
				step = 0
				while True:
					# Debug mode
					if options.debug:
						[predMask, gtMask] = sess.run([predictedMask, inputBatchMasks], feed_dict={datasetSelectionPlaceholder: TRAIN})
						print ("Prediction shape: %s | GT shape: %s" % (str(predMask.shape), str(gtMask.shape)))
						assert (predMask.shape == gtMask.shape), "Error: Prediction and ground-truth shapes don't match"
						if np.isnan(np.sum(predMask)):
							print ("Error: NaN encountered!")
							exit (-1)

						print ("Unique labels in prediction:", np.unique(predMask))
						print ("Unique labels in GT:", np.unique(gtMask))

						# Verify end point shapes
						for endPointName in endPoints:
							endPointOutput = sess.run(endPoints[endPointName], feed_dict={datasetSelectionPlaceholder: TRAIN})
							print ("End point: %s | Shape: %s" % (endPointName, str(endPointOutput.shape)))

					# Run optimization op (backprop)
					if options.tensorboardVisualization:
						_, summary = sess.run([applyGradients, mergedSummaryOp], feed_dict={datasetSelectionPlaceholder: TRAIN})
						summaryWriter.add_summary(summary, global_step=globalStep) # Write logs at every iteration
					else:
						_ = sess.run(applyGradients, feed_dict={datasetSelectionPlaceholder: TRAIN})
					
					if step % options.displayStep == 0:
						# Calculate batch loss
						[fileName, originalImage, trainLoss, predictedSegMask] = sess.run([inputBatchImageNames, inputBatchImages, loss, predictedMask], feed_dict={datasetSelectionPlaceholder: TRAIN})
						print ("Epoch: %d | Iteration: %d | Minibatch Loss: %f" % (epoch, step, trainLoss))

						# Save image results
						writeMaskToImage(originalImage, predictedSegMask, options.trainImagesOutputDirectory, fileName)

					step += 1
					globalStep += 1

			except tf.errors.OutOfRangeError:
				print('Done training for %d epochs, %d steps.' % (epoch, step))

			if step % options.saveStep == 0:
				# Save model weights to disk
				outputFileName = os.path.join(options.outputModelDir, options.outputModelName)
				saver.save(sess, outputFileName)
				print ("Model saved: %s" % (outputFileName))

			# Check the accuracy on validation set
			sess.run(valIterator.initializer)
			averageValLoss = 0.0
			iterations = 0
			try:
				while True:
					[fileName, originalImage, valLoss, predictedSegMask] = sess.run([inputBatchImageNames, inputBatchImages, loss, predictedMask], feed_dict={datasetSelectionPlaceholder: VAL})
					
					# Save image results
					writeMaskToImage(originalImage, predictedSegMask, options.valImagesOutputDirectory, fileName)

					print ("Iteration: %d | Validation loss: %f" % (iterations, valLoss))
					averageValLoss += valLoss
					iterations += 1

			except tf.errors.OutOfRangeError:
				print('Evaluation on validation set completed!')

			averageValLoss /= iterations
			print('Average validation loss: %f' % (averageValLoss))

			# # Check the accuracy on test data
			# if step % options.saveStepBest == 0:
			# 	# Report loss on test data
			# 	[testLoss] = sess.run([loss], feed_dict={inputBatchImages: batchImagesTest, inputBatchLabels: batchLabelsTest, inputKeepProbability: 1.0})
			# 	print ("Test loss: %f" % testLoss)

			# 	# If its the best loss achieved so far, save the model
			# 	if testLoss < bestLoss:
			# 		bestLoss = testLoss
			# 		# bestModelSaver.save(sess, best_checkpoint_dir + 'checkpoint.data')
			# 		bestModelSaver.save(sess, checkpointPrefix, global_step=0, latest_filename=checkpointStateName)
			# 		print ("Best model saved in file: %s" % checkpointPrefix)
			# 	else:
			# 		print ("Previous best accuracy: %f" % bestLoss)

		# Save final model weights to disk
		outputFileName = os.path.join(options.outputModelDir, options.outputModelName)
		saver.save(sess, outputFileName)
		print ("Model saved: %s" % (outputFileName))

		# Report loss on test data
		sess.run(testIterator.initializer)
		averageTestLoss = 0.0
		iterations = 0
		try:
			while True:
				[fileName, originalImage, testLoss, predictedSegMask] = sess.run([inputBatchImageNames, inputBatchImages, loss, predictedMask], feed_dict={datasetSelectionPlaceholder: TEST})
				
				# Save image results
				writeMaskToImage(originalImage, predictedSegMask, options.testImagesOutputDirectory, fileName)

				print ("Iteration: %d | Test loss: %f" % (iterations, testLoss))
				averageTestLoss += testLoss
				iterations += 1

		except tf.errors.OutOfRangeError:
			print('Evaluation on test set completed!')

		averageTestLoss /= iterations
		print('Average test loss: %f' % (averageTestLoss))

		print ("Optimization completed!")


if options.testModel:
	print ("Testing saved model")

	if os.path.exists(options.testImagesOutputDirectory):
		shutil.rmtree(options.testImagesOutputDirectory)
	os.makedirs(options.testImagesOutputDirectory)
	
	# Now we make sure the variable is now a constant, and that the graph still produces the expected result.
	with tf.Session(config=config) as sess:
		modelFileName = os.path.join(options.outputModelDir, options.outputModelName)
		# saver = tf.train.import_meta_graph(modelFileName + ".meta")
		saver.restore(sess, modelFileName)

		# # Get reference to placeholders
		# outputMaskNode = sess.graph.get_tensor_by_name("predictedMasks:0")
		# lossNode = sess.graph.get_tensor_by_name("Loss/totalLoss:0")
		# datasetSelectionPlaceholderNode = sess.graph.get_tensor_by_name("DatasetSelectionPlaceholder:0")

		sess.run(testIterator.initializer)
		iterations = 0
		averageTestLoss = 0.0
		try:
			while True:
				[fileName, originalImage, testLoss, predictedSegMask. predictedSegLogits] = sess.run([inputBatchImageNames, inputBatchImages, loss, predictedMask, predictedLogits], feed_dict={datasetSelectionPlaceholder: TEST})

				# Save image results
				writeMaskToImage(originalImage, predictedSegMask, options.testImagesOutputDirectory, fileName)
				
				if options.useCRFPostProcessing:
					# TODO: Incorporate dense CRF
					unary = predictedSegLogits[0]
					unary = -np.log(unary)
					unary = unary.transpose(1, 0, 2)
					w, h, c = unary.shape
					unary = unary.transpose(1, 0, 2).reshape(options.numClasses, -1)
					unary = np.ascontiguousarray(unary)
					resizedImg = np.ascontiguousarray(originalImage[0]).astype(np.uint8)

					d = dcrf.DenseCRF2D(w, h, options.numClasses)
					d.setUnaryEnergy(unary)
					d.addPairwiseBilateral(sxy=5, srgb=3, rgbim=resizedImg, compat=1)

					q = d.inference(50)
					mask = np.argmax(q, axis=0).reshape(w, h).transpose(1, 0)
					mask = np.array(mask, dtype=np.uint8)[np.newaxis, :, :, np.newaxis]
					
					# Save image results
					writeMaskToImage(originalImage, mask, options.testImagesOutputDirectory, fileName, append='-crf')

				print ("Iteration: %d | Test loss: %f" % (iterations, testLoss))
				averageTestLoss += testLoss
				iterations += 1

		except tf.errors.OutOfRangeError:
			print('Evluation on test set completed!')

		averageTestLoss /= iterations
		print('Average test loss: %f' % (averageTestLoss))

	print ("Model evaluation completed!")
