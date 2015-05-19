#! /usr/bin/env python

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
import sys
import os
import binascii
from struct import pack_into, unpack_from
from string import rfind
from time import gmtime, strftime

# TFTF section types
TFTF_SECTION_TYPE_RESERVED = 0x00
TFTF_SECTION_TYPE_RAW_CODE = 0x01
TFTF_SECTION_TYPE_RAW_DATA = 0x02
TFTF_SECTION_TYPE_COMPRESSED_CODE = 0x03
TFTF_SECTION_TYPE_COMPRESSED_DATA = 0x04
TFTF_SECTION_TYPE_MANIFEST = 0x05
TFTF_SECTION_TYPE_SIGNATURE = 0x80
TFTF_SECTION_TYPE_CERTIFICATE = 0x81
TFTF_SECTION_TYPE_END_OF_DESCRIPTORS = 0xfe  # (File End)

# These types are considered valid
valid_tftf_types = \
    (TFTF_SECTION_TYPE_RAW_CODE,
     TFTF_SECTION_TYPE_RAW_DATA,
     TFTF_SECTION_TYPE_COMPRESSED_CODE,
     TFTF_SECTION_TYPE_COMPRESSED_DATA,
     TFTF_SECTION_TYPE_MANIFEST,
     TFTF_SECTION_TYPE_SIGNATURE,
     TFTF_SECTION_TYPE_CERTIFICATE,
     TFTF_SECTION_TYPE_END_OF_DESCRIPTORS)

# These types contribute to the TFTF load_length and extended_length
# calculation.
# NB. any certificates located after the first signature block are
# excluded from the calculations
countable_tftf_types = \
    (TFTF_SECTION_TYPE_RAW_CODE,
     TFTF_SECTION_TYPE_RAW_DATA,
     TFTF_SECTION_TYPE_COMPRESSED_CODE,
     TFTF_SECTION_TYPE_COMPRESSED_DATA,
     TFTF_SECTION_TYPE_MANIFEST,
     TFTF_SECTION_TYPE_CERTIFICATE)


# Other TFTF header constants (mostly field sizes)
TFTF_SENTINEL = "TFTF"
TFTF_TIMESTAMP_LENGTH = 16
TFTF_FW_PKG_NAME_LENGTH = 48
TFTF_HDR_LENGTH = 512
TFTF_SECTION_HDR_LENGTH = 16
TFTF_PADDING = 12
TFTF_MAX_SECTIONS = 25

# Offsets into the TFTF header
TFTF_HDR_OFF_SENTINEL = 0x00
TFTF_HDR_OFF_TIMESTAMP = 0x04
TFTF_HDR_OFF_NAME = 0x14
TFTF_HDR_OFF_LENGTH = 0x44
TFTF_HDR_OFF_SECTIONS = 0x64  # Start of sections array


# TFTF Signature Block layout
TFTF_SIGNATURE_KEY_NAME_LENGTH = 64
TFTF_SIGNATURE_KEY_HASH_LENGTH = 32
TFTF_SIGNATURE_OFF_LENGTH = 0x00
TFTF_SIGNATURE_OFF_TYPE = 0x04
TFTF_SIGNATURE_OFF_KEY_NAME = 0x08
TFTF_SIGNATURE_OFF_KEY_HASH = 0x48
TFTF_SIGNATURE_OFF_KEY_SIGNATURE = 0x68
# Size of the fixed portion of the signature block
TFTF_SIGNATURE_BLOCK_SIZE = TFTF_SIGNATURE_OFF_KEY_SIGNATURE

# TFTF Signature Types and associated dictionary of types and names
# NOTE: When adding new types, both the "define" and the dictionary
# need to be updated.
TFTF_SIGNATURE_TYPE_UNKNOWN = 0x00
TFTF_SIGNATURE_TYPE_RSA_2048_SHA_256 = 0x01
tftf_signature_types = {"rsa2048-sha256": TFTF_SIGNATURE_TYPE_RSA_2048_SHA_256}

TFTF_FILE_EXTENSION = ".bin"

# TFTF validity assesments
TFTF_VALID = 0
TFTF_INVALID = 1
TFTF_VALID_WITH_COLLISIONS = 2

# Size of the blob to copy each time
copy_blob_size = 1024*1024*10

section_names = {
    TFTF_SECTION_TYPE_RESERVED: "Reserved",
    TFTF_SECTION_TYPE_RAW_CODE: "Code",
    TFTF_SECTION_TYPE_RAW_DATA: "Data",
    TFTF_SECTION_TYPE_COMPRESSED_CODE: "Compressed code",
    TFTF_SECTION_TYPE_COMPRESSED_DATA: "Compressed data",
    TFTF_SECTION_TYPE_MANIFEST: "Manifest",
    TFTF_SECTION_TYPE_SIGNATURE: "Signature",
    TFTF_SECTION_TYPE_CERTIFICATE: "Certificate",
    TFTF_SECTION_TYPE_END_OF_DESCRIPTORS: "End of descriptors",
}


def warning(*objs):
    print("WARNING: ", *objs, file=sys.stderr)


def error(*objs):
    print("ERROR: ", *objs, file=sys.stderr)


class TftfSection:
    """TFTF Section representation"""

    def __init__(self, section_type, section_length=0,
                 extended_length=0, copy_offset=0, filename=None):
        """Constructor

        If filename is specified, this reads in the file and sets the section
        length to the length of the file.
        """
        self.section_length = section_length
        self.expanded_length = extended_length
        self.copy_offset = copy_offset
        self.section_type = section_type
        self.filename = filename

        # Try to size the section length from the section input file
        if filename:
            try:
                statinfo = os.stat(filename)
                # TODO: Lengths will be different if/when we support
                # compression:
                # - section_length will shrink to the compressed size
                # - expanded_length will remain the input file length
                self.section_length = statinfo.st_size
                self.expanded_length = statinfo.st_size
            except:
                error("file" + filename + " is invalid or missing")

    def unpack(self, section_buf, section_offset):
        # Unpack a section header from a TFTF header buffer, and return
        # a flag indicating if the section was a section-end

        section_hdr = unpack_from("<LLLL", section_buf, section_offset)
        self.section_length = section_hdr[0]
        self.expanded_length = section_hdr[1]
        self.copy_offset = section_hdr[2]
        self.section_type = section_hdr[3]
        return self.section_type in valid_tftf_types

    def pack(self, buf, offset):
        # Pack a section header into a TFTF header buffer at the specified
        # offset, returning the offset of the next section.

        pack_into("<LLLL", buf, offset,
                  self.section_length,
                  self.expanded_length,
                  self.copy_offset,
                  self.section_type)
        return offset + TFTF_SECTION_HDR_LENGTH

    def update(self, copy_offset):
        # Update a section header at the specifed offset, and return an
        # offset to the start of the next section
        #
        # Use this to sweep through the list of sections and update the
        # section copy_offsets to concatenate sections (except where the
        # user has specified an offset).

        if self.section_type in countable_tftf_types:
            if self.copy_offset == 0:
                self.copy_offset = copy_offset
        else:
            self.copy_offset = 0

        return self.copy_offset + self.expanded_length

    def section_name(self, section_type):
        # Convert a section type into textual form

        if section_type in section_names:
            return section_names[section_type]
        else:
            return "?"

    def display_table_header(self, indent):
        # Print the section table column names, returning the column
        # header for the section table (no indentation)

        print("{0:s}     Length     Exp. Len   Offset     Type".format(indent))

    def display(self, indent, index, expand_type):
        # Print a section header

        section_string = "{0:s}  {1:2d} ".format(indent, index)
        section_string += "0x{0:08x} 0x{1:08x} 0x{2:08x} 0x{3:08x}".format(
                          self.section_length, self.expanded_length,
                          self.copy_offset, self.section_type)

        if expand_type:
            section_string += " ({0:s})".format(
                              self.section_name(self.section_type))
        print(section_string)

    def display_binary_data(self, blob, show_all, indent=""):
        """Display a binary blob"""

        # Print the data blob
        length = len(blob)
        max_on_line = 32

        if length <= (3 * max_on_line) or show_all:
            for start in range(0, length, max_on_line):
                num_bytes = min(length, max_on_line)
                foo = binascii.hexlify(blob[start:start+num_bytes])
                print("{0:s}{1:s}".format(indent, foo))
        else:
            # Blob too long, so print the first and last lines with a ":"
            # spacer between
            print("{0:s}{1:s}".format(
                indent,
                binascii.hexlify(blob[0:max_on_line])))
            print("{0:s}  :".format(indent))
            start = length - max_on_line
            print("{0:s}{1:s}".format(
                indent,
                binascii.hexlify(blob[start:length])))

    def display_data(self, blob, title=None, indent=""):
        """Display the payload referenced by a single TFTF header"""

        # Print the title line
        title_string = indent
        if title:
            title_string += title
        title_string += "({0:d} bytes): {1:s}".format(
                        self.section_length,
                        self.section_name(self.section_type))
        print(title_string)

        # Print the data blob
        if self.section_type == TFTF_SECTION_TYPE_SIGNATURE:
            # Signature blocks have a known format which we can break down
            # for the user
            key_hash = blob[TFTF_SIGNATURE_OFF_KEY_HASH:
                            TFTF_SIGNATURE_OFF_KEY_SIGNATURE]
            sig_block = unpack_from("<LL64s", blob, 0)
            print("{0:s}  Length:    {1:08x}".format(indent, sig_block[0]))
            print("{0:s}  Sig. type: {1:d}".format(indent, sig_block[1]))
            print("{0:s}  Key name:".format(indent))
            print("{0:s}      '{1:4s}'".format(indent, sig_block[2]))
            print("{0:s}  Key hash:".format(indent))
            print("{0:s}      {1:s}".format(indent,
                                            binascii.hexlify(key_hash)))
            print("{0:s}    Signature:".format(indent))
            self.display_binary_data(blob[TFTF_SIGNATURE_OFF_KEY_SIGNATURE:],
                                     True, indent + "        ")
        else:
            # The default is to show the blob as a binary dump.
            self.display_binary_data(blob, False, indent + "  ")
        print("")


class Tftf:
    """TFTF representation"""

    def __init__(self, filename=None):
        # Private fields
        self.tftf_buf = bytearray(TFTF_HDR_LENGTH)
        self.collisions = []
        self.collisions_found = False
        self.header_validity = TFTF_INVALID
        self.tftf_length = 0  # length of the whole blob

        # Header fields
        self.sentinel = 0
        self.timestamp = ""
        self.firmware_package_name = ""
        self.load_length = 0
        self.load_base = 0
        self.expanded_length = 0
        self.start_location = 0
        self.unipro_mfg_id = 0
        self.unipro_pid = 0
        self.ara_vid = 0
        self.ara_pid = 0
        self.sections = []

        if filename:
            # Load the TFTF buffer and parse it for the TFTF header and
            # section list
            self.load_tftf_file(filename)
        else:
            # Salt the list with the end-of-table, because we will be
            # adding sections manually later
            eot = TftfSection(TFTF_SECTION_TYPE_END_OF_DESCRIPTORS,
                              0, 0, 0, None)
            self.sections.append(eot)

    def load_tftf_file(self, filename):
        """Try to import a TFTF header and/or file

        If "buf" is None, then we only import the TFTF header.  However, if
        buf is supplied (typically a memoryview into a larger buffer), the
        entire TFTF file is also imported into the buffer.  This is to allow
        for cases where the caller needs to determine the TFTF characteristics
        before creating their buffer.
        """

        success = True
        if filename:
            # Try to open the file, and if that fails, try appending the
            # extension.
            names = (filename, filename + TFTF_FILE_EXTENSION)
            rf = None
            for name in names:
                try:
                    rf = open(name, 'rb')
                    break
                except:
                    print("can't find TFTF file"), filename
                    success = False

            if success:
                # Record the length of the entire TFTF blob (this will be
                # longer than the header's load_length)
                rf.seek(0, 2)
                self.tftf_length = rf.tell()

                rf.seek(0, 0)
                # (Display-tftf case) Read the entire TFTF file into
                # a local buffer
                self.tftf_buf = bytearray(self.tftf_length)
                rf.readinto(self.tftf_buf)
                rf.close()
                self.unpack()
        return success

    def load_tftf_from_buffer(self, buf):
        """Import a TFTF blob from a memory buffer"""

        self.tftf_buf = buf
        self.unpack()

    def unpack(self):
        # Unpack a TFTF header from a buffer

        tftf_hdr = unpack_from("<4s16s48sLLLLLLLL", self.tftf_buf)
        self.sentinel = tftf_hdr[0]
        self.timestamp = tftf_hdr[1]
        self.firmware_package_name = tftf_hdr[2]
        self.load_length = tftf_hdr[3]
        self.load_base = tftf_hdr[4]
        self.expanded_length = tftf_hdr[5]
        self.start_location = tftf_hdr[6]
        self.unipro_mfg_id = tftf_hdr[7]
        self.unipro_pid = tftf_hdr[8]
        self.ara_vid = tftf_hdr[9]
        self.ara_pid = tftf_hdr[10]

        # Purge (the EOT from) the list because we're populating the entire
        # list from the file
        self.sections = []

        # Parse the table of section headers
        section_offset = TFTF_HDR_OFF_SECTIONS
        for section_index in range(TFTF_MAX_SECTIONS):
            section = TftfSection(0, 0, 0, 0, None)
            if section.unpack(self.tftf_buf, section_offset):
                self.sections.append(section)
                section_offset += TFTF_SECTION_HDR_LENGTH

                if section.section_type == \
                   TFTF_SECTION_TYPE_END_OF_DESCRIPTORS:
                    break
            else:
                str = "Invalid section type {0:02x} "\
                      "at [{1:d}]".format(section.section_type,
                                          section_index)
                error(str)
                break
        self.sniff_test()

    def pack(self):
        # Pack the TFTF header members into the TFTF header buffer, prior
        # to writing the buffer out to a file.

        # Populate the fixed part of the TFTF header.
        # (Note that we need to break up the packing because the "s" format
        # doesn't zero-pad a string shorter than the field width)
        pack_into("<4s16s", self.tftf_buf, 0,
                  self.sentinel,
                  self.timestamp)
        if self.firmware_package_name:
            pack_into("<48s", self.tftf_buf, TFTF_HDR_OFF_NAME,
                      self.firmware_package_name)
        pack_into("<LLLLLLLL", self.tftf_buf, TFTF_HDR_OFF_LENGTH,
                  self.load_length,
                  self.load_base,
                  self.expanded_length,
                  self.start_location,
                  self.unipro_mfg_id,
                  self.unipro_pid,
                  self.ara_vid,
                  self.ara_pid)

        # Pack the section headers into the TFTF header buffer
        offset = TFTF_HDR_OFF_SECTIONS
        for section in self.sections:
            offset = section.pack(self.tftf_buf, offset)

    def add_section(self, section_type, section_data, copy_offset=0):
        # Add a new section to the section table and return a success flag
        #
        # (This would be called by "sign-tftf" to add signature and
        # certificate blocks.)

        num_sections = len(self.sections)
        if num_sections < TFTF_MAX_SECTIONS:
            # Insert the section to the section list, just in front of
            # the end-of-table marker.
            #
            # Notes:
            #   1. We assume this is an uncompressable section
            #   2. We defer pushing the new section into the buffer until
            #      the write stage or someone explicitly calls "pack".)
            self.sections.insert(num_sections - 1,
                                 TftfSection(section_type,
                                             len(section_data),
                                             len(section_data),
                                             copy_offset, None))

            # Append the section data blob to our TFTF buffer
            self.tftf_buf += section_data

            # Record the length of the entire TFTF blob (this will be longer
            # than the header's load_length)
            self.tftf_length = len(self.tftf_buf)
            return True
        else:
            error("Section table full")
            return False

    def add_section_from_file(self, section_type, filename, copy_offset=0):
        # Add a new section from a file and return a success flag
        #
        # (This would be called by "create-tftf" while/after parsing section
        # parameters)

        if len(self.sections) < TFTF_MAX_SECTIONS:
            try:
                with open(filename, 'rb') as readfile:
                    section_data = readfile.read()

                return self.add_section(section_type, section_data,
                                        copy_offset)
            except:
                error("Unable to read" + filename)
                return False
        else:
            error("Section table full")
            return False

    def update_section_table_offsets(self):
        # Update the copy_offsets in the section table and the load_length
        #
        # (This would be called by "create-tftf" after parsing all of the
        # parameters)

        self.load_length = 0
        self.expanded_length = 0
        copy_offset = 0
        for section in self.sections:
            # Fill in any omitted section copy_offsets
            section_start = copy_offset
            copy_offset = section.update(copy_offset)

            # Only count the sections that are (or could be) signed
            if section.section_type == TFTF_SECTION_TYPE_SIGNATURE or \
               section.section_type == TFTF_SECTION_TYPE_END_OF_DESCRIPTORS:
                break

            # The load_length and expanded_length are calculated as being
            # the maximum extent of the various sections
            if section.section_type in countable_tftf_types:
                section_end = section_start + section.section_length
                expanded_end = section_start + section.expanded_length
                self.load_length = max(self.load_length, section_end)
                self.expanded_length = max(self.expanded_length, expanded_end)

    def check_for_collisions(self):
        # Scan the TFTF section table for collisions
        #
        # This would be called by "create-ffff" after parsing all of the
        # parameters and calling update_ffff_sections().

        for comp_a in range(len(self.sections)):
            collision = []
            # extract sections[comp_a]
            section_a = self.sections[comp_a]
            if section_a.section_type == TFTF_SECTION_TYPE_SIGNATURE or \
               section_a.section_type == TFTF_SECTION_TYPE_END_OF_DESCRIPTORS:
                break

            start_a = section_a.copy_offset
            end_a = start_a + section_a.expanded_length - 1
            for comp_b in range(len(self.sections)):
                # skip checking one's self
                if comp_a != comp_b:
                    # extract sections[comp_b]
                    section_b = self.sections[comp_b]
                    if section_b.section_type == \
                       TFTF_SECTION_TYPE_SIGNATURE or \
                       section_b.section_type == \
                       TFTF_SECTION_TYPE_END_OF_DESCRIPTORS:
                        break

                    start_b = section_b.copy_offset
                    end_b = start_b + section_b.expanded_length - 1
                    if end_b >= start_a and \
                       start_b <= end_a:
                        self.collisions_found = True
                        collision += [comp_b]
            self.collisions += [collision]
        return self.collisions_found

    def sniff_test(self):
        # Perform a quick validity check of the TFTF header.  Generally
        # done when importing an existing TFTF file.

        self.header_validity = TFTF_VALID

        # Valid sentinel? (This should also subsume the "erased block" test)
        if self.sentinel != TFTF_SENTINEL:
            self.header_validity = TFTF_INVALID
        else:
            # check for collisions
            if self.check_for_collisions():
                self.header_validity = TFTF_VALID_WITH_COLLISIONS

        return self.header_validity

    def is_good(self):
        # Go/no-go decision on a TFTF header

        return self.header_validity != TFTF_INVALID

    def post_process(self):
        """Post-process the TFTF header

        Process the TFTF header (called by "create-tftf" after processing all
        arguments)
        """

        self.sentinel == TFTF_SENTINEL

        # Update the section table copy_offsets and check for collisions
        self.sentinel = TFTF_SENTINEL
        self.update_section_table_offsets()
        self.check_for_collisions()
        if self.timestamp == "":
            self.timestamp = strftime("%Y%m%d %H%M%S", gmtime())

        # Trim the name to length
        if self.firmware_package_name:
            self.firmware_package_name = \
                self.firmware_package_name[0:TFTF_FW_PKG_NAME_LENGTH]

        # Determine the validity
        self.sniff_test()

    # Create/write the TFTF file
    #
    #  Returns True on success, False on failure
    #
    def write(self, out_filename):
        """Create the TFTF file and return a success flag

        Create the TFTF file (appending the default extension if omitted)
        and write the TFTF buffer to it.
        """

        success = True
        # Prepare the output buffer
        self.pack()

        # Record the length of the entire TFTF blob (this will be longer
        # than the header's load_length)
        self.tftf_length = len(self.tftf_buf)

        # Ensure the output file ends in the default TFTF file extension if
        # the user hasn't specified their own extension.
        if rfind(out_filename, ".") == -1:
            out_filename += TFTF_FILE_EXTENSION

        try:
            with open(out_filename, 'wb') as wf:
                # Write the TFTF header
                wf.write(self.tftf_buf)

            # verify the file is the correct length
            try:
                statinfo = os.stat(out_filename)
                if statinfo.st_size != self.tftf_length:
                    error(out_filename + "has wrong length")
            except:
                error("Can't get info on" + out_filename)

        except:
            error("Unable to write" + out_filename)
            success = False
        else:
            if success:
                print("Wrote" + out_filename)
            else:
                error("Failed to write" + out_filename)
            return success

    def display(self, title=None, indent=""):
        """Display a single TFTF header"""

        # Dump the contents of the fixed part of the TFTF header
        if title:
            print("{0:s}TFTF Header for {1:s} ({2:d} bytes)".format(
                indent, title, self.tftf_length))
        else:
            print("{0:s}TFTF Header ({1:d} bytes)".format(
                indent, self.tftf_length))
        print("{0:s}  Sentinel:         '{1:4s}'".format(
            indent, self.sentinel))
        print("{0:s}  Timestamp:        '{1:16s}'".format(
            indent, self.timestamp))
        print("{0:s}  Fw. pkg name:     '{1:48s}'".format(
            indent, self.firmware_package_name))
        print("{0:s}  Load length:       0x{1:08x}".format(
            indent, self.load_length))
        print("{0:s}  Load base:         0x{1:08x}".format(
            indent, self.load_base))
        print("{0:s}  Expanded length:   0x{1:08x}".format(
            indent, self.expanded_length))
        print("{0:s}  Start location:    0x{1:08x}".format(
            indent, self.start_location))
        print("{0:s}  Unipro mfg ID:     0x{1:08x}".format(
            indent, self.unipro_mfg_id))
        print("{0:s}  Unipro product ID: 0x{1:08x}".format(
            indent, self.unipro_pid))
        print("{0:s}  Ara vendor ID:     0x{1:08x}".format(
            indent, self.ara_vid))
        print("{0:s}  Ara product ID:    0x{1:08x}".format(
            indent, self.ara_pid))

        # Dump the table of section headers
        print("{0:s}  Section Table:".format(indent))
        self.sections[0].display_table_header(indent)
        for index in range(len(self.sections)):
            section = self.sections[index]
            section.display(indent, index, True)

            # Note any collisions on a separate line
            # NB. the end-of-table section does not have a collisions list.
            if index < len(self.collisions) and \
               len(self.collisions[index]) > 0:
                section_string = \
                    "{0:s}     Collides with section(s):".format(indent)
                for collision in self.collisions[index]:
                    section_string += " {0:d}".format(collision)
                print(section_string)

        # Note any unused sections
        num_unused_sections = TFTF_MAX_SECTIONS - len(self.sections)
        if num_unused_sections > 1:
            print("{0:s}  {1:2d} (unused)".format(indent, len(self.sections)))
        if num_unused_sections > 2:
            print("{0:s}   :    :".format(indent))
        if num_unused_sections > 0:
            print("{0:s}  {1:2d} (unused)".format(indent, TFTF_MAX_SECTIONS-1))
        print(" ")

    def display_data(self, title=None, indent=""):
        """Display the payload referenced by a single TFTF header"""

        # Print the title line
        title_string = "{0:s}TFTF contents".format(indent)
        if title:
            title_string += " for {0:s}".format(title)
        title_string += " ({0:d} bytes)".format(self.tftf_length)
        print(title_string)

        # Print the associated data blobs
        offset = TFTF_HDR_LENGTH
        for index in range(len(self.sections)):
            section = self.sections[index]
            if section.section_type == TFTF_SECTION_TYPE_END_OF_DESCRIPTORS:
                break
            end = offset + section.section_length - 1
            section.display_data(self.tftf_buf[offset:end],
                                 "section [{0:d}] ".format(index),
                                 indent + "  ")
            offset += section.section_length

    def find_first_section(self, section_type):
        """Find the index of the first section of the specified type

        Return the index of the first section in the section table matching
        the specified type. Returns the index of the first end-of-table
        marker if not found.

        (Typically used to find the first signature section as part of the
        signing operation.)
        """

        for index in range(len(self.sections)):
            section = self.sections[index]
            if section.section_type == section_type or \
               section.section_type == TFTF_SECTION_TYPE_END_OF_DESCRIPTORS:
                return index
        return len(self.sections)

    def get_header_up_to_section(self, section_index):
        """Return the head of the header buffer up to section_table[index]

        Returns a (binary) string consisting of the first N bytes of the
        header buffer up to the start of the Ith entry in the section
        descriptor table.

        (Typically used to obtain the first part of the blob to be signed.)
        """

        if section_index > len(self.sections):
            return None

        # Flush any changes out to the buffer and return the substring
        self.pack()
        slice_end = TFTF_HDR_OFF_SECTIONS + \
            section_index * TFTF_SECTION_HDR_LENGTH
        return self.tftf_buf[0:slice_end]

    def get_section_data_up_to_section(self, section_index):
        """Return the section info for the first N sections

        Returns a (binary) string consisting of the first N bytes of the
        section data up to the start of the Ith entry in the section
        table.

        (Typically used to obtain the second part of the blob to be signed.)
        """

        if section_index > len(self.sections):
            return None

        # Flush any changes out to the buffer and return the substring
        self.pack()
        slice_end = TFTF_HDR_LENGTH
        for index in range(section_index):
            section = self.sections[index]
            slice_end += section.section_length
        return self.tftf_buf[TFTF_HDR_LENGTH:slice_end]
