import numpy as np
import tifffile.tifffile as tifffile
import re
import cv2 as cv
import time
import os
import pandas as pd
import warnings


class Channel(object):
    """
    Base Class to keep track of one channel (x,y,t) of microscopy data.

    Channel Objects are created from tifffile.Tifffile and act as shallow copies of the
    TiffPage objects making up the channel,
    until a Nupy array is generated by 'getArray'. Then self.array is populated by a Numpy array from the
    raw image data, using the 'asarray' function in 'tiffile.Pages'. Only a single z-slice
    and channel are handled per Channel object. A reference to the base 'tifffile.Tifffile' is stored in
    self.tif.

    There are currently two very similar subclasses of Channel, MM_Channel, and IJ_Channel to handle
    Micromanager OME-TIFFs and ImageJ hyperstacks, respectively.


    """
    def __init__(self, chIndex, tiffFile, name, sliceIndex=0):
        """
        :param chIndex: index of channel to create, 0-based.
        :type chIndex: int
        :param tiffFile: TiffFile object to extract channel from
        :type tiffFile: :class:'tifffile'
        :param name: name of channel, used in Analysis output
        :type name: str
        :param sliceIndex: z-slice to extract, defaults to 0
        :type sliceIndex: int

        """
        self.chIndex = chIndex
        self.sliceIdx = sliceIndex
        self.tif = tiffFile
        self.name = name
        self.tif_ij_metadata = tifffile.imagej_metadata
        self.pxSize_um = self._read_px_size()
        self.finterval_ms = self._read_finteval() #frame interval from settings, maybe not actual
        self.pages = self._page_extractor()
        self.elapsedTimes_ms = self._extractElapsedTimes() # only MM files have real values, IJ files trust finterval
        self.array = np.empty((0)) # getArray populates this when called
        self.actualFrameIntervals_ms = None #getActualFrameIntervals_ms populates this when called

    def _extractElapsedTimes(self):
        """
        Returns a list of elapsed times in ms from the start of image acquisition.

        Values are extracted from image MM metadata timestamps. Note that this is only possible for MicroManager based
        Channels (and other timestamped formats). Since ImageJ does not store this information, the frame interval value
        is trusted and used to calculate elapsed times.

        :return: Timestamps of channel frames from the start of the acquisition.
        :rtype: list

        """
        out = []

        if self.tif.is_micromanager:

            for page in self.pages:
                out.append(page.tags["MicroManagerMetadata"].value["ElapsedTime-ms"])

            return out

        else:
            elapsed = 0
            for page in self.pages:
                out.append(elapsed)
                elapsed += self.finterval_ms

            return out

    def _page_extractor(self):
        """
        Decides which TiffPages from the Tiffile belon to the channel and stores them in self.pages

        Tifffile objects read the actual TIF image data together with the associated TIF-tags from disk and
        encapsulates them in TiffPage objects. The TiffPages that make up the channel data are read and stored in this
        method.

        Tifffile has an option to read TiffFrame objects instead of TiffPage objects. TiffFrames are light weight
        versions of TiffPages, but they do not contain any TIF-tags. This method ensures that TiffPages are returned by
        setting Tifffile.pages.useframes to _False_.

        :return: TiffPage objects corresponding to the chosen slice and channel
        :rtype: list
        """

        self.tif.pages.useframes = False  # TiffFrames can't be used for extracting metadata
        out = []

        if self.tif.is_micromanager:

            sliceMap = self.tif.micromanager_metadata["IndexMap"]["Slice"]
            channelMap = self.tif.micromanager_metadata["IndexMap"]["Channel"]

        elif self.tif.is_imagej:

            indexMap = self._ij_pagemapper()
            sliceMap = indexMap[1]
            channelMap = indexMap[0]

        for i in range(len(self.tif.pages)):

            if (sliceMap[i] == self.sliceIdx) and (channelMap[i] == self.chIndex):
                page = self.tif.pages[i]
                out.append(page)

        return out

    def _ij_pagemapper(self):
        """
        Helper method to make maps for sorting IJ Pages in to slices and channels.

        :returns: (channelMap, sliceMap, frameMap) which are lists of integers describing which channel,
        slice, frame each Tiff-Page belongs to. Indexes start at 0.

        """
        nChannels = self.tif.imagej_metadata.get('channels', 1)
        nSlices = self.tif.imagej_metadata.get('slices', 1)
        #nFrames = self.tif.imagej_metadata.get('frames', None)

        channelMap = []
        sliceMap = []
        frameMap = []

        for i in range(len(self.tif.pages)):
            chIdx = (i % nChannels)
            slIdx = (i // nChannels) % nSlices
            frIdx = ( i // (nChannels*nSlices))


            channelMap.append(int(chIdx))
            sliceMap.append(int(slIdx))
            frameMap.append(int(frIdx))

        return channelMap, sliceMap, frameMap

    def _read_px_size(self):
        """
        Reads and returns pixel size from metadata

        Determines which version of MM that was used to acquire the data, or if it is an ImageJ file.
        MM versions 1.4 and 2.0-gamma, share Metadata structure, but 2.0.0-beta is slightly different
        in where the pixel size can be read from. In 2.0-beta the pixel size is read from
        tif.micromanager_metadata['PixelSize_um'], but in versions  1.4 and 2.0-gamma it is read from
        tif.micromanager_metadata['Summary']['PixelSize_um']

        If Channel is based on an ImageJ tif file, then pixel size is read from the XResolution tif tag, and the
        unit is derived from ij_metadata. The method will do its best to transform the size unit in to microns.

        The following size unit strings are detected:
            centimeter_strings = ['cm', 'centimeter', 'centimeters']
            millimeter_strings = ['mm', 'millimeter', 'millimeters']
            micrometer_strings = ['\\u00B5m', 'um', 'micrometer', 'micron']

        :return: pixel size in um
        :rtype: float
        """
        if self.tif.is_micromanager:
            # if the file is a MM file this branch determines which version
            one4_regex = re.compile("1\.4\.[\d]")  # matches 1.4.digit
            gamma_regex = re.compile("gamma")      # matches "gamma"
            beta_regex = re.compile("beta")        # matches "beta"

            version = self.tif.micromanager_metadata["Summary"]["MicroManagerVersion"]

            if (re.search(beta_regex, version) != None):
                px_size_um = self.tif.micromanager_metadata['PixelSize_um']

                return px_size_um

            elif (re.search(one4_regex, version) != None):
                px_size_um = self.tif.micromanager_metadata['Summary']['PixelSize_um']

                return px_size_um

            elif (re.search(gamma_regex, version) != None):
                px_size_um = self.tif.micromanager_metadata['Summary']['PixelSize_um']

                return px_size_um

        elif self.tif.is_imagej:
            #this is not as clean due to the undocumented nature of imageJ metadata
            #IJ uses TIF-tag to store pixel size information, but does not confer to standard unit of 'cm' or 'inch'

            divisor, dividend = self.tif.pages[0].tags['XResolution'].value
            if divisor == 0:
                raise ValueError("Divisor is 0, something is wrong in the tif XResolution tag!")
            px_size = float(dividend/divisor)

            centimeter_strings = ['cm', 'centimeter', 'centimeters']
            millimeter_strings = ['mm', 'millimeter', 'millimeters']
            micrometer_strings = ['\\u00B5m', 'um', 'micrometer', 'micron']

            #sz_unit defaults to µm if not set in IJ metadata
            sz_unit = self.tif.imagej_metadata.get('unit', '\\u00B5m')

            if (sz_unit in centimeter_strings):
                px_size_um = px_size * 10 * 1000 #10 mm/cm * 1000 um/mm = 10000 um

            elif (sz_unit in millimeter_strings):
                px_size_um = px_size * 1000  # 1000 um/mm

            elif (sz_unit in micrometer_strings):
                px_size_um = px_size

            return px_size_um

        else:
            raise ValueError("No pixel size found!")

    def _read_finteval(self):
        """
        Reads frame interval from metadata

        Determines which version of MM that was used to acquire the data, or if it is an ImageJ file.
        MM versions 1.4 and 2.0-gamma, share Metadata structure, but 2.0.0-beta is slightly different
        in where the frame interval and pixel sizes can be read from. In 2.0-beta the
        frame interval is read from tif.micromanager_metadata['Summary']['WaitInterval'],
        and in 1.4/2.0-gamma it is read from tif.micromanager_metadata['Summary']['Interval_ms']

        MM versions used for testing:
          MicroManagerVersion 1.4.23 20180220
          MicroManagerVersion 2.0.0-gamma1 20190527
          MicroManagerVersion 2.0.0-beta3 20180923

        If the data set is from ImageJ the method will do its best to transform the time unit in to ms

        The following tunit strings are detected:
          minute_strings = ['min', 'mins', 'minutes', 'm']
          hour_strings = ['hour', 'hours', 'h']
          second_strings = ['seconds', 'sec', 's']

        :return: frame interval as recorded in metadata in milliseconds
        :rtype: int or float
        """
        if self.tif.is_micromanager:
            # if the file is a MM file this branch determines which version
            one4_regex = re.compile("1\.4\.[\d]")  # matches 1.4.d
            gamma_regex = re.compile("gamma")
            beta_regex = re.compile("beta")

            version = self.tif.micromanager_metadata["Summary"]["MicroManagerVersion"]


            if (re.search(beta_regex, version) != None):
                finterval_ms = self.tif.micromanager_metadata['Summary']['WaitInterval']

                return finterval_ms

            elif (re.search(one4_regex, version) != None):
                finterval_ms = self.tif.micromanager_metadata['Summary']['Interval_ms']

                return finterval_ms

            elif (re.search(gamma_regex, version) != None):
                finterval_ms = self.tif.micromanager_metadata['Summary']['Interval_ms']

                return finterval_ms

        elif self.tif.is_imagej:
            # this is not as clean due to the undocumented nature of imageJ metadata

            minute_strings = ['min', 'mins', 'minutes', 'm']
            hour_strings = ['hour', 'hours', 'h']
            second_strings = ['seconds', 'sec', 's']

            finterval = self.tif.imagej_metadata.get('finterval', 1)

            # tunit defaults to 's' if not present in IJ-metadata
            tunit = self.tif.imagej_metadata.get('tunit', 's')

            if (tunit in minute_strings):
                finterval_ms = 60 * 1000 * finterval

            elif (tunit in hour_strings):
                finterval_ms = 60 * 60 * 1000 * finterval

            elif (tunit in second_strings):
                finterval_ms = 1000 * finterval

            return finterval_ms

        else:
            raise ValueError("No frame interval found!")

    def getPages(self):
        """
        Returns the TiffPages that make up the channel data

        :return: a list of the TiffPages extraxted from the Tifffile used to create the Channel
        :rtype: list
        """

        return self.pages

    def getElapsedTimes_ms(self):
        """
        Returns a list of elapsed times in ms from the start of image acquisition.

        Values are extracted from image timestamps. Note that this is only possible for MicroManager based
        Channels (and other timestamped formats). Since ImageJ does not store this information the frame interval value
        is trusted and used to calculate elapsed times.

        :return: Timestamps of channel frames from the start of the acquisition.
        :rtype: list

        """
        return self.elapsedTimes_ms

    def getArray(self):
        """
        Returns channel image data as a numpy array.

        Method populates the array from self.pages first time it is called.

        :return: Channel image data as 3D-numpy array
        :rtype: numpy.ndarray (type depends of original format)
        """

        if len(self.array) != 0:

            return self.array

        else:
            outshape = (len(self.pages),
                        self.pages[0].shape[0],
                        self.pages[0].shape[1])

            outType = self.pages[0].asarray().dtype

        out = np.empty(outshape, outType)

        for i in range(len(self.pages)):
            out[i] = self.pages[i].asarray()

        return out

    def getTemporalMedianChannel(self, **kwargs):
        """
        Returns a new MedianChannel object where self.array has been replaced with temporal median filtered channel data

        kwargs and defaults are: {doGlidingProjection = True, frameSamplingInterval=3, startFrame=0, stopFrame=None}
        Defaults to a gliding 3 frame temporal median of the whole channel if no kwargs are given.

        :return: A MedianChannel object based on the current channel wher self.array has been replaced by a nupy array
                 of the type float32 representing the temporal median of Channel data.
        :rtype: MedianChannel

        """

        return MedianChannel(self, **kwargs)

    def getTiffFile(self):
        '''

        Returns the `Tifffile` objedt that the `Channel` is based on.

        :return: Tifffile-object used when Channel was created
        :rtype: object tifffile.Tifffile

        '''

        return self.tif

    def getActualFrameIntevals_ms(self):
        """
        Returns the intervals between frames in ms as a 1D numpy array.

        Note that the first value is set to 0 by definition, please consider this when calculating average actual frame
        interval. Returns None if only one frame exists in the channel. Values are calculated the first time the method
        is called.

        :return: 1D numpy array of time intervals between frames
        :rtype: numpy.ndarray

        """


        if (self.actualFrameIntervals_ms != None):

            return self.actualFrameIntervals_ms

        elif len(self.pages) == 1:

            return None

        else:
            out = []
            t0 = self.elapsedTimes_ms[0]
            for t in self.elapsedTimes_ms[1:]:
                out.append(t-t0)
                t0 = t
            return np.asarray(out)

    def getIntendedFrameInterval_ms(self):
        """
        Returns the intended frame interval as recorded in image metadata.

        :return: interval between successive frames in ms
        :rtype: int
        """

        return self.finterval_ms

    def doFrameIntervalSanityCheck(self, maxDiff=0.01):
        """
        Performs sanity check on frame intervals.

        Checks if the intended frame interval from metadata matches the actual frame interval from individual frame time
        stamps. If the mean difference is more than maxDiff the function returns ``False``. Defaults to allowing a
        1% difference between mean actual frame interval and intended frame interval by default.

        :param maxDiff: Maximum allowed difference between actual frame intervals and the intended interval, expressed as a fraction.
        :type maxDiff: float
        :return: True if the fraction of actual and intended frame intervals is below maxDiff.
        :rtype: bool

        """


        if len(self.pages) == 1:
            return None

        elif (self.getIntendedFrameInterval_ms() == 0):

            return False
        else:
            fract = self.getActualFrameIntevals_ms().mean()/self.getIntendedFrameInterval_ms()
            out = abs(1-fract) < maxDiff

            return out

    def rehapeMedianFramesTo6d(self):
        #reshapes 3D (t, x, y) array to (t, 1, 1, x, y, 1) for saving dimensions in TZCYXS order
        shape = self.medianArray.shape
        self.medianArray.shape = (shape[0], 1, 1, shape[1], shape[2], 1)

class MedianChannel(Channel):
    """
    A subclass of channel where the channel array has been temporal median filtered.

    Temporal median filtering is very useful when performing optical flow based analysis of time lapse microscopy data
    beacuse it filters out fast moving free-floating debree from the dataset. Note that the median array will be
    shorter than the original array. In the default case if a temporal median of 3 frames is applied, the the output
    array will contain 3-1 = 2 frames less than the input if a gliding projection (default) is performed.

    """

    def __init__(self, channel, doGlidingProjection = True, frameSamplingInterval=3, startFrame=0, stopFrame=None):
        """
        :param channel: Parent Channel object for the MedianChannel
        :type channel: Channel
        :param frameSamplingInterval: How many frames to use in temporal median projection

        """
        #fields specific for MedianChannel
        self.parent_channnel = channel
        self.doGlidingProjection = doGlidingProjection
        self.frameSamplingInterval = frameSamplingInterval
        self.startFrame = startFrame
        if stopFrame == None:
            self.stopFrame = len(channel.pages)
        else:
            self.stopFrame = stopFrame

        #fields common to all Channel-type objects
        self.chIndex = self.parent_channnel.chIndex
        self.sliceIdx = self.parent_channnel.sliceIdx
        self.tif = self.parent_channnel.tif
        self.name = self.parent_channnel.name
        self.tif_ij_metadata = self.parent_channnel.tif_ij_metadata
        self.pxSize_um = self.parent_channnel.pxSize_um
        self.finterval_ms = self.parent_channnel.finterval_ms
        self.elapsedTimes_ms = self._recalculate_elapsed_times()
        self.pages = self.parent_channnel.pages
        self.array = self.doTemporalMedianFilter(doGlidingProjection=self.doGlidingProjection,
                                                 startFrame=self.startFrame,
                                                 stopFrame=self.stopFrame,
                                                 frameSamplingInterval=self.frameSamplingInterval
                                                 )
        self.actualFrameIntervals_ms = self.parent_channnel.getActualFrameIntevals_ms()


    def _recalculate_elapsed_times(self):
        #TODO
        return []

    def doTemporalMedianFilter(self, doGlidingProjection, startFrame, stopFrame,
                               frameSamplingInterval):
        """
        Calculates a temporal median filter of the Channel.

        The function runs a gliding N-frame temporal median on every pixel to
        smooth out noise and to remove fast moving debris that is not migrating
        cells.

        :param doGlidingProjection: Should a gliding (default) or staggered projection be performed?
        :type doGlidingProjection: bool
        :param stopFrame: Last frame to analyze, defaults to analyzing all frames if ``None``.
        :type stopFrame: int
        :param startFrame: First frame to analyze.
        :type startFrame: int
        :param frameSamplingInterval: Do median projection every N frames.
        :type frameSamplingInterval: int

        :return: 1 if successful.
        :rtype: bool


        """

        if (stopFrame == None) or (stopFrame > len(self.pages)):
            raise ValueError("StopFrame cannot be None or larger than number of frames!")

        if (startFrame >= stopFrame):
            raise ValueError("StartFrame cannot be larger than or equal to Stopframe!")

        if (stopFrame-startFrame < frameSamplingInterval):
            raise ValueError("Not enough frames selected to do median projection! ")

        arr = self.parent_channnel.getArray()

        if doGlidingProjection:
            nr_outframes = (stopFrame - startFrame) - (frameSamplingInterval - 1)

        else:
            nr_outframes = int((stopFrame - startFrame) / frameSamplingInterval)

        outshape = (nr_outframes, arr.shape[1], arr.shape[2])
        outframe = 0
        # Filling a pre-created array is computationally cheaper
        self.medianArray = np.ndarray(outshape, dtype=np.float32)

        if doGlidingProjection:
            for inframe in range(startFrame, stopFrame-frameSamplingInterval+1):

                # median of frames n1,n2,n3...
                frame_to_store = np.median(arr[inframe:inframe + frameSamplingInterval], axis=0).astype(np.float32)

                self.medianArray[outframe] = frame_to_store
                outframe += 1
        else:
            for inframe in range(startFrame, stopFrame, frameSamplingInterval):
                # median of frames n1,n2,n3...
                frame_to_store = np.median(arr[inframe:inframe + frameSamplingInterval], axis=0).astype(np.float32)

                self.medianArray[outframe] = frame_to_store
                outframe += 1

        return 1


def normalization_to_8bit(image_stack, lowPcClip = 0.175, highPcClip = 0.175):
    """
    Function to rescale 16/32/64 bit arrays to 8-bit for visualizing output

    Defaults to saturate 0.35% of pixels, 0.175% in each end by default, which often produces nice results. This
    is the same as pressing 'Auto' in the ImageJ contrast manager. numpy.interp() linear interpolation is used
    for the mapping.

    :param image_stack: Numpy array to be rescaled
    :type image_stack: Numpy array
    :param lowPcClip: Fraction for black clipping bound
    :type lowPcClip: float
    :param highPcClip: Fraction for white/saturated clipping bound
    :type highPcClip: float
    :return: 8-bit numpy array of the same shape as :param image_stack:
    :rtype: numpy.dtype('uint8')
    """


    #clip image to saturate 0.35% of pixels 0.175% in each end by default.
    low = int(np.percentile(image_stack, lowPcClip))
    high = int(np.percentile(image_stack, 100 - highPcClip))

    # use linear interpolation to find new pixel values
    image_equalized = np.interp(image_stack.flatten(), (low, high), (0, 255))

    return image_equalized.reshape(image_stack.shape).astype('uint8')


def read_micromanager(tif):
    """
    returns metadata from a micromanager file
    """
    pass




