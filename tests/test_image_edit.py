"""Tests for image editing functionality (Flux.2 Klein 4B)."""
import unittest
from unittest.mock import patch, MagicMock
import base64
import json


class TestSdCppEditImage(unittest.TestCase):
    """Test SdCppModule.edit_image() method."""

    def setUp(self):
        self.mock_app = MagicMock()
        self.mock_app.config = {
            'SD_WRAPPER_URL': 'http://flai-sd:7861',
            'SD_CPP_TIMEOUT': 900,
            'SD_MODEL_TYPE': 'z_image_turbo',
        }

        # Create minimal image (1x1 PNG base64)
        # Minimal valid PNG 1x1 pixel
        self.test_image_b64 = base64.b64encode(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
            b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
            b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05'
            b'\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        ).decode('utf-8')

    @patch('modules.sd_cpp.requests.post')
    def test_edit_image_success(self, mock_post):
        """Test successful image editing."""
        from modules.sd_cpp import SdCppModule

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'data': [{'b64_json': self.test_image_b64}]
        }
        mock_post.return_value = mock_response

        module = SdCppModule(self.mock_app)

        edit_data = {
            'edit_prompt': 'change the sky to sunset',
            'strength': 0.7,
        }
        result = module.edit_image(edit_data, self.test_image_b64, lang='en')

        self.assertTrue(result['success'])
        self.assertEqual(result['gen_model'], 'flux-2-klein-4b')
        self.assertIsNotNone(result['image_data'])

    @patch('modules.sd_cpp.requests.post')
    def test_edit_image_timeout(self, mock_post):
        """Test edit timeout handling."""
        from modules.sd_cpp import SdCppModule
        import requests

        mock_post.side_effect = requests.exceptions.Timeout()

        module = SdCppModule(self.mock_app)

        edit_data = {'edit_prompt': 'change sky', 'strength': 0.7}
        result = module.edit_image(edit_data, self.test_image_b64, lang='ru')

        self.assertFalse(result['success'])
        self.assertIn('timeout', result['error'].lower())

    @patch('modules.sd_cpp.requests.post')
    def test_edit_image_connection_error(self, mock_post):
        """Test connection error handling."""
        from modules.sd_cpp import SdCppModule
        import requests

        mock_post.side_effect = requests.exceptions.ConnectionError()

        module = SdCppModule(self.mock_app)

        edit_data = {'edit_prompt': 'change sky', 'strength': 0.7}
        result = module.edit_image(edit_data, self.test_image_b64, lang='ru')

        self.assertFalse(result['success'])

    def test_edit_image_unavailable(self):
        """Test editing when module is unavailable."""
        from modules.sd_cpp import SdCppModule

        self.mock_app.config['SD_WRAPPER_URL'] = ''
        module = SdCppModule(self.mock_app)

        edit_data = {'edit_prompt': 'change sky', 'strength': 0.7}
        result = module.edit_image(edit_data, self.test_image_b64, lang='en')

        self.assertFalse(result['success'])


class TestMultimodalEditParams(unittest.TestCase):
    """Test MultimodalModule.generate_edit_params()."""

    @patch('modules.multimodal.format_prompt')
    def test_edit_params_json_parsing(self, mock_format):
        """Test that edit params are correctly parsed from model response."""
        mock_format.return_value = 'Edit this image'

        from modules.multimodal import MultimodalModule
        mock_llamacpp = MagicMock()
        mock_llamacpp.available = True
        mock_llamacpp.chat_with_image.return_value = '''
        {
            "edit_prompt": "make the sky blue",
            "strength": 0.7,
            "mask": "",
            "preserve": "main subject"
        }
        '''

        module = MultimodalModule(MagicMock())
        module.llamacpp = mock_llamacpp

        params, error = module.generate_edit_params(
            'make sky blue', 'base64data', lang='en'
        )

        self.assertIsNone(error)
        self.assertEqual(params['edit_prompt'], 'make the sky blue')
        self.assertEqual(params['strength'], 0.7)
        self.assertEqual(params['preserve'], 'main subject')


if __name__ == '__main__':
    unittest.main()
