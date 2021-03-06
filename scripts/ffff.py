#! /usr/bin/python

#
# Copyright (c) 2015 Google Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
# contributors may be used to endorse or promote products derived from this
# software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
# OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

from __future__ import print_function
from time import gmtime, strftime
from struct import unpack_from, pack_into
from ffff_element import FFFF_HDR_LENGTH, FFFF_HDR_VALID, \
    FFFF_MAX_HEADER_BLOCK_SIZE, FFFF_HDR_OFF_TAIL_SENTINEL, \
    FFFF_HDR_OFF_ELEMENT_TBL, FFFF_MAX_ELEMENTS, FfffElement, \
    FFFF_ELT_LENGTH, FFFF_HDR_OFF_FLASH_IMAGE_NAME, \
    FFFF_HDR_OFF_FLASH_CAPACITY, FFFF_FLASH_IMAGE_NAME_LENGTH, \
    FFFF_ELEMENT_END_OF_ELEMENT_TABLE, FFFF_HEADER_COLLISION, \
    FFFF_HDR_ERASED, FFFF_SENTINEL, \
    FFFF_HDR_INVALID
import sys

from util import error, is_power_of_2, next_boundary, is_constant_fill, \
    PROGRAM_SUCCESS, PROGRAM_WARNINGS, PROGRAM_ERRORS

def header_block_size(erase_block_size):
    # Determine the size of the FFFF header block
    #
    # The size is defined as some power-of-2 * the erase-block-size

    for size in range(erase_block_size, FFFF_MAX_HEADER_BLOCK_SIZE):
        if size > FFFF_HDR_LENGTH:
            return size;

# FFFF representation
#
class Ffff:
    """Defines the contents of a Flash Format for Firmware (FFFF) header

    Each FFFF header contains general information about the Flash chip,
    the range of it consumed by the FFFF, and a table of Elements (see:
    ffff_element.py) which describe the purpose and layout of each
    populated blob.

    The overall FFFF ROMimage contains 2 headers, one in each of the first
    2 erasable blocks (see: ffff_romimage.py).
    """

    def __init__(self, buf, offset, flash_image_name, flash_capacity,
                 erase_block_size, image_length, header_generation_number):
        """FFFF constructor"""
        # FFFF header fields
        self.sentinel = ""
        self.timestamp = ""
        self.flash_image_name = flash_image_name
        self.flash_capacity = flash_capacity
        self.erase_block_size = erase_block_size
        self.header_size = FFFF_HDR_LENGTH
        self.flash_image_length = image_length
        self.header_generation_number = header_generation_number
        self.elements = []
        self.padding = ""
        self.tail_sentinel = ""

        # Private vars
        self.ffff_buf = buf
        self.header_offset = offset
        self.collisions = []
        self.collisions_found = False
        self.duplicates = []
        self.duplicates_found = False
        self.invalid_elements_found = False
        self.header_validity = FFFF_HDR_VALID
        self.element_location_min = 2 * self.header_block_size()
        self.element_location_max = image_length

    def header_block_size(self):
        return header_block_size(self.erase_block_size)

    def unpack(self):
        """Unpack an FFFF header from a buffer"""

        ffff_hdr = unpack_from("<16s16s48sLLLLL", self.ffff_buf,
                               self.header_offset)
        self.sentinel = ffff_hdr[0]
        self.timestamp = ffff_hdr[1]
        self.flash_image_name = ffff_hdr[2]
        self.flash_capacity = ffff_hdr[3]
        self.erase_block_size = ffff_hdr[4]
        self.header_size = ffff_hdr[5]
        self.flash_image_length = ffff_hdr[6]
        self.header_generation_number = ffff_hdr[7]

        # unpack the tail sentinel
        ffff_hdr = unpack_from("<16s", self.ffff_buf,
                               self.header_offset + FFFF_HDR_OFF_TAIL_SENTINEL)
        self.tail_sentinel = ffff_hdr[0]

        # Determine the ROM range that can hold the elements
        self.element_location_min = 2 * self.header_block_size()
        self.element_location_max = self.flash_capacity

        # Parse the table of element headers
        self.elements = []
        offset = FFFF_HDR_OFF_ELEMENT_TBL
        for index in range(FFFF_MAX_ELEMENTS):
            element = FfffElement(index,
                                  self.ffff_buf,
                                  self.flash_capacity,
                                  self.erase_block_size,
                                  0, 0, 0, 0, 0)
            if not element.unpack(self.ffff_buf, offset):
                self.elements.append(element)
                offset += FFFF_ELT_LENGTH
            else:
                # Stop on the first unused element
                print("unpack done")
                break
        self.validate_ffff_header()

    def pack(self):
        # Pack the FFFF header members into a FFFF header buffer, prior
        # to writing the buffer out to a file.

        # Populate the fixed part of the FFFF header.
        # (Note that we need to break up the packing because the "s" format
        # won't zero-pad a string shorter than the field width)
        timestamp = strftime("%Y%m%d %H%M%S", gmtime())
        pack_into("<16s16s", self.ffff_buf, self.header_offset,
                  self.sentinel, timestamp)
        if self.flash_image_name:
            pack_into("<48s", self.ffff_buf,
                      self.header_offset + FFFF_HDR_OFF_FLASH_IMAGE_NAME,
                      self.flash_image_name)
        pack_into("<LLLLL", self.ffff_buf,
                  self.header_offset + FFFF_HDR_OFF_FLASH_CAPACITY,
                  self.flash_capacity,
                  self.erase_block_size,
                  FFFF_HDR_LENGTH,
                  self.flash_image_length,
                  self.header_generation_number)
        pack_into("<16s", self.ffff_buf,
                  self.header_offset + FFFF_HDR_OFF_TAIL_SENTINEL,
                  self.tail_sentinel)

        # Pack the element headers into the FFFF header buffer
        offset = self.header_offset + FFFF_HDR_OFF_ELEMENT_TBL
        for element in self.elements:
            offset = element.pack(self.ffff_buf, offset)

    def add_element(self, element_type, element_id, element_generation,
                    element_location, element_length, filename):
        """Add a new element to the element table

        Adds an element to the element table but doesn't load the TFTF
        file into the ROMimage buffer.  That is done later by post_process.
        Returns a success flag

        (We would typically be called by "create-ffff" after parsing element
        parameters.)
        """
        if len(self.elements) < FFFF_MAX_ELEMENTS:
            element = FfffElement(len(self.elements),
                                  self.ffff_buf,
                                  self.flash_capacity,
                                  self.erase_block_size,
                                  element_type,
                                  element_id,
                                  element_generation,
                                  element_location,
                                  element_length,
                                  filename)
            if element.init():
                self.elements.append(element)

                span_start = element.element_location
                span_end = span_start + len(element.tftf_blob.tftf_buf)
                self.ffff_buf[span_start:span_end] = element.tftf_blob.tftf_buf
                return True
            else:
                return False
        else:
            error("too many elements")
            return False

    def validate_element_table(self):
        # Check for element validity, inter-element collisions and
        # duplicate elements
        #
        # (This would be called by "create-tftf" after parsing all of the
        # parameters and calling update_tftf_elements()).

        self.collisions = []
        self.collisions_found = False
        self.duplicates = []
        self.duplicates_found = False
        self.invalid_elements_found = False

        for i, elt_a in enumerate(self.elements):
            collision = []
            duplicate = []

            if elt_a.element_type == FFFF_ELEMENT_END_OF_ELEMENT_TABLE:
                break

            # Check for an invalid element (i.e., either munged or
            # collides with the 2 FFFF header blocks
            if not elt_a.validate(self.element_location_min,
                                  self.element_location_max):
                self.invalid_elements_found = True
            if elt_a.element_location < (2 * self.header_block_size()):
                self.collisions_found = True
                collision += [FFFF_HEADER_COLLISION]
                error("Element at location " + \
                    format(elt_a.element_location, "#x") + \
                    " collides with two header blocks of size " + \
                    format(2 * self.header_block_size(), "#x"))

            for j, elt_b in enumerate(self.elements):
                # skip checking one's self
                if i != j:
                    # extract elements[j]
                    if elt_b.element_type == \
                            FFFF_ELEMENT_END_OF_ELEMENT_TABLE:
                        break

                    # check for collision
                    elt_a.validate_against(elt_b)
                    start_a = elt_a.element_location
                    end_a = start_a + elt_a.element_length - 1
                    start_b = elt_b.element_location
                    end_b = start_b + elt_b.element_length - 1
                    if end_b >= start_a and start_b <= end_a:
                        self.collisions_found = True
                        collision += [j]

                    # check for other duplicate entries
                    # Per the specification: "At most, one element table
                    # entry with a particular element type, element ID,
                    # and element genration may be present in the element
                    # table."
                    if elt_a.element_type == elt_b.element_type and \
                       elt_a.element_id == elt_b.element_id and \
                       elt_a.element_generation == \
                       elt_b.element_generation:
                        self.duplicatess_found = True
                        duplicate += [j]

            self.collisions += [collision]
            self.duplicates += [duplicate]
        if self.collisions_found:
            error("Found collisions in FFFF element table!")
        if self.duplicates_found:
            error("Found duplicates in FFFF element table!")
        if self.invalid_elements_found:
            error("Found invalid elements in FFFF element table!")
        return not self.collisions_found and \
            not self.duplicates_found and \
            not self.invalid_elements_found

    def validate_ffff_header(self):
        # Perform a quick validity check of the header.  Generally done when
        # importing an existing FFFF file.

        self.header_validity = FFFF_HDR_VALID

        # Check for erased header

        span = self.ffff_buf[self.header_offset:
                             self.header_offset+FFFF_HDR_LENGTH]
        if is_constant_fill(span, 0) or \
           is_constant_fill(span, 0xff):
            error("FFFF header validates as erased.")
            self.header_validity = FFFF_HDR_ERASED
            return self.header_validity

        # Valid sentinels?
        if self.sentinel != FFFF_SENTINEL or \
                self.tail_sentinel != FFFF_SENTINEL:
            error("Invalid sentinel")
            self.header_validity = FFFF_HDR_INVALID
            return self.header_validity

        # Verify sizes
        if not is_power_of_2(self.erase_block_size):
            error("Erase block size must be 2**n")
            self.header_validity = FFFF_HDR_INVALID
            return self.header_validity
        elif (self.flash_image_length % self.erase_block_size) != 0:
            error("Image length is not a multiple of erase bock size")
            self.header_validity = FFFF_HDR_INVALID
            return self.header_validity

        # Verify that the unused portions of the header are zeroed, per spec.
        span_start = self.header_offset + FFFF_HDR_OFF_ELEMENT_TBL + \
            len(self.elements) * FFFF_ELT_LENGTH
        span_end = self.header_offset + FFFF_HDR_OFF_TAIL_SENTINEL - \
            span_start
        if not is_constant_fill(self.ffff_buf[span_start:span_end], 0):
            error("Unused portions of header are not zeroed: " + str(span_start) + \
                " to " + str(span_end))
            self.header_validity = FFFF_HDR_INVALID
            return self.header_validity

        # check for elemental problems
        if not self.validate_element_table():
            error("Invalid element table.")
            self.header_validity = FFFF_HDR_INVALID

        return self.header_validity

    def post_process(self, buf):
        """Post-process the FFFF header

        Process the FFFF header, assigning unspecified element locations to
        be contiguous (on erase-block-size boundaries), and read the TFTF
        files into the buffer at those locations.

        (Called by "create-ffff" after processing all arguments)
        """
        # Revalidate the erase block size
        self.erase_block_mask = self.erase_block_size - 1

        # Scan the elements and fill in missing start locations.
        # Elements are concatenated at the granuarity of the erase block size
        location = self.elements[0].element_location
        for element in self.elements:
            if element.element_location == 0:
                element.element_location = location
                error("Note: Assuming element [{0:d}]"
                      " loads at {1:08x}".format(element.index, location))
            if self.flash_image_length != 0 and \
               element.element_location + element.element_length >= \
               self.flash_image_length:
                error("--element-location " + format(element.element_location, "#x") + \
                    " + --element-length " + format(element.element_length, "#x") + \
                    " exceeds --image-length " + format(self.flash_image_length, "#x"))
                sys.exit(PROGRAM_ERRORS)
            location = next_boundary(element.element_location +
                                     element.element_length,
                                     self.erase_block_size)

        if self.flash_image_length == 0:
            self.flash_image_length = location

        self.validate_element_table()

        # fill in and/or trim selected FFFF fields
        self.sentinel = FFFF_SENTINEL

        self.timestamp = strftime("%Y%m%d %H%M%S", gmtime())
        if self.flash_image_name:
            self.flash_image_name = \
                self.flash_image_name[0:FFFF_FLASH_IMAGE_NAME_LENGTH]
        self.tail_sentinel = FFFF_SENTINEL

        # Flush the structure elements to the FFFF buffer and do a final
        # sniff test on the results
        self.pack()
        self.validate_ffff_header()

    def same_as(self, other):
        """Determine if this FFFF is identical to another"""
        # Compare the element tables
        elements_match = len(self.elements) == len(other.elements)
        if elements_match:
            for i in range(len(self.elements)):
                if not self.elements[i].same_as(other.elements[i]):
                    return False

        # If the element tables matched, compare the rest of the header
        return elements_match and \
            self.sentinel == other.sentinel and \
            self.timestamp == other.timestamp and \
            self.flash_image_name == other.flash_image_name and \
            self.flash_capacity == other.flash_capacity and \
            self.erase_block_size == other.erase_block_size and \
            self.header_size == other.header_size and \
            self.flash_image_length == other.flash_image_length and \
            self.header_generation_number == \
            other.header_generation_number and \
            self.padding == other.padding and \
            self.tail_sentinel == other.tail_sentinel

    def write_elements(self, header):
        """Write all the elements to separate files

        (This is intended to support ffff-display's --explode option)
        """
        for index, element in enumerate(self.elements):
            filename = "{0:s}_element_{1:d}".format(header, index)
            element.write(filename)
            if element.element_type == FFFF_ELEMENT_END_OF_ELEMENT_TABLE:
                break

    def display_element_table(self):
        # Display the FFFF header's element table in human-readable form

        print("  Element Table:")
        self.elements[0].display_table_header()
        for index, element in enumerate(self.elements):
            element.display(True)
            if element.element_type == FFFF_ELEMENT_END_OF_ELEMENT_TABLE:
                break

        # Note any unused elements
        num_unused_elements = FFFF_MAX_ELEMENTS - len(self.elements)
        if num_unused_elements > 1:
            print("  {0:2d} (unused)".format(len(self.elements)))
            print("   :    :")
        if num_unused_elements > 0:
            print("  {0:2d} (unused)".format(FFFF_MAX_ELEMENTS-1))

    def display_element_data(self, header_index):
        # Display the element data (TFTFs) from the element table
        if header_index:
            print("Elements for FFFF Header[{0:d}]:".
                  format(header_index))
        else:
            print("Elements for both FFFF headers:")

        for element in self.elements:
            element.display_element_data(header_index)
            if element.element_type == FFFF_ELEMENT_END_OF_ELEMENT_TABLE:
                break

    def display(self, header_index, display_element_data,
                use_common_header, filename=None):
        """Display an FFFF header"""
        # Dump the contents of the fixed part of the TFTF header
        if filename:
            print("FFFF Header[{0:d}] for: {1:s}".
                  format(header_index, filename))
        else:
            print("FFFF Header[{0:d}]:".format(header_index))
        print("  Sentinel:             '{0:16s}'".
              format(self.sentinel))
        print("  Timestamp:            '{0:16s}'".
              format(self.timestamp))
        print("  Flash image name:     '{0:48s}'".
              format(self.flash_image_name))
        print("  Flash capacity:       0x{0:08x}".
              format(self.flash_capacity))
        print("  Erase block size:     0x{0:08x}".
              format(self.erase_block_size))
        print("  Header size:          0x{0:08x}".
              format(self.header_size))
        print("  Flash image_length:   0x{0:08x}".
              format(self.flash_image_length))
        print("  Header generation:    0x{0:08x} ({0:d})".
              format(self.header_generation_number))

        # Dump the element table
        self.display_element_table()

        # Bring up the rear
        print("  Sentinel:             '{0:16s}'".format(self.tail_sentinel))
        print(" ")

        # Dump the element table's TFTF information
        if display_element_data:
            if use_common_header:
                self.display_element_data(None)
            else:
                self.display_element_data(header_index)
        print(" ")
