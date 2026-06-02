# frozen_string_literal: true

# Formula for Smith.
class Smith < Formula
  include Language::Python::Virtualenv

  desc "Read-only source-of-truth investigation CLI for AI agents"
  homepage "https://github.com/faustodavid/smith"
  url "https://github.com/faustodavid/smith.git",
      tag:      "v0.1.0",
      revision: "a36a75ea9b84e9bcd27d6308903cdb29776986f4"
  license "MIT"
  head "https://github.com/faustodavid/smith.git", branch: "main"

  depends_on "maturin" => :build
  depends_on "pkgconf" => :build
  depends_on "rust" => :build
  depends_on "libffi"
  depends_on "libyaml"
  depends_on "openssl@3"
  depends_on "python@3.14"
  depends_on "ripgrep"

  resource "setuptools" do
    url "https://files.pythonhosted.org/packages/9d/76/f789f7a86709c6b087c5a2f52f911838cad707cc613162401badc665acfe/setuptools-82.0.1-py3-none-any.whl"
    sha256 "a59e362652f08dcd477c78bb6e7bd9d80a7995bc73ce773050228a348ce2e5bb"
  end

  resource "packaging" do
    url "https://files.pythonhosted.org/packages/20/12/38679034af332785aac8774540895e234f4d07f7545804097de4b666afd8/packaging-25.0-py3-none-any.whl"
    sha256 "29572ef2b1f17581046b3a2227d5c611fb25ec70ca1ba8554b24b0e69331a484"
  end

  resource "wheel" do
    url "https://files.pythonhosted.org/packages/87/22/b76d483683216dde3d67cba61fb2444be8d5be289bf628c13fc0fd90e5f9/wheel-0.46.3-py3-none-any.whl"
    sha256 "4b399d56c9d9338230118d705d9737a2a468ccca63d5e813e2a4fc7815d8bc4d"
  end

  resource "semantic-version" do
    url "https://files.pythonhosted.org/packages/6a/23/8146aad7d88f4fcb3a6218f41a60f6c2d4e3a72de72da1825dc7c8f7877c/semantic_version-2.10.0-py2.py3-none-any.whl"
    sha256 "de78a3b8e0feda74cabc54aab2da702113e33ac9d9eb9d2389bcf1f58b7d9177"
  end

  resource "setuptools-rust" do
    url "https://files.pythonhosted.org/packages/f9/7b/d05b1778f2d4e354d103e3421c6267d923032fefcc5ca5b7df0cb21cefd0/setuptools_rust-1.12.0-py3-none-any.whl"
    sha256 "7e7db90547f224a835b45f5ad90c983340828a345554a9a660bdb2de8605dcdd"
  end

  resource "flit-core" do
    url "https://files.pythonhosted.org/packages/f2/65/b6ba90634c984a4fcc02c7e3afe523fef500c4980fec67cc27536ee50acf/flit_core-3.12.0-py3-none-any.whl"
    sha256 "e7a0304069ea895172e3c7bb703292e992c5d1555dd1233ab7b5621b5b69e62c"
  end

  resource "cython" do
    url "https://files.pythonhosted.org/packages/e5/41/54fd429ff8147475fc24ca43246f85d78fb4e747c27f227e68f1594648f1/cython-3.2.3-py3-none-any.whl"
    sha256 "06a1317097f540d3bb6c7b81ed58a0d8b9dbfa97abf39dfd4c22ee87a6c7241e"
  end

  resource "pathspec" do
    url "https://files.pythonhosted.org/packages/cc/20/ff623b09d963f88bfde16306a54e12ee5ea43e9b597108672ff3a408aad6/pathspec-0.12.1-py3-none-any.whl"
    sha256 "a0d503e138a4c123b27490a4f7beda6a01c6f288df0e4a8b79c7eb0dc7b4cc08"
  end

  resource "pluggy" do
    url "https://files.pythonhosted.org/packages/54/20/4d324d65cc6d9205fabedc306948156824eb9f0ee1633355a8f7ec5c66bf/pluggy-1.6.0-py3-none-any.whl"
    sha256 "e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746"
  end

  resource "trove-classifiers" do
    url "https://files.pythonhosted.org/packages/7c/a4/81502f486f01db95bc8320646a8a12511f5e556cb63d5e224d91816605c4/trove_classifiers-2026.6.1.19-py3-none-any.whl"
    sha256 "ab4c4ec93cc4a4e7815fa759906e05e6bb3f2fbd92ea0f897288c6a43efd15b3"
  end

  resource "hatchling" do
    url "https://files.pythonhosted.org/packages/08/e7/ae38d7a6dfba0533684e0b2136817d667588ae3ec984c1a4e5df5eb88482/hatchling-1.27.0-py3-none-any.whl"
    sha256 "d3a2f3567c4f926ea39849cdf924c7e99e6686c9c8e288ae1037c8fa2a5d937b"
  end

  resource "setuptools-scm" do
    url "https://files.pythonhosted.org/packages/ab/ac/8f96ba9b4cfe3e4ea201f23f4f97165862395e9331a424ed325ae37024a8/setuptools_scm-8.3.1-py3-none-any.whl"
    sha256 "332ca0d43791b818b841213e76b1971b7711a960761c5bea5fc5cdb5196fbce3"
  end

  resource "hatch-vcs" do
    url "https://files.pythonhosted.org/packages/82/0f/6cbd9976160bc334add63bc2e7a58b1433a31b34b7cda6c5de6dd983d9a7/hatch_vcs-0.4.0-py3-none-any.whl"
    sha256 "b8a2b6bee54cf6f9fc93762db73890017ae59c9081d1038a41f16235ceaf8b2c"
  end

  resource "azure-core" do
    url "https://files.pythonhosted.org/packages/a6/f3/b416179e408990df5db0d516283022dde0f5d0111d98c1a848e41853e81c/azure_core-1.41.0.tar.gz"
    sha256 "f46ff5dfcd230f25cf1c19e8a34b8dc08a337b2503e268bb600a16c00db8ad5a"
  end

  resource "azure-identity" do
    url "https://files.pythonhosted.org/packages/c5/0e/3a63efb48aa4a5ae2cfca61ee152fbcb668092134d3eb8bfda472dd5c617/azure_identity-1.25.3.tar.gz"
    sha256 "ab23c0d63015f50b630ef6c6cf395e7262f439ce06e5d07a64e874c724f8d9e6"
  end

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/f3/ce/ee2ecad540810a79593028e88299baeae54d346cc7a0d94b6199988b89b1/certifi-2026.5.20.tar.gz"
    sha256 "69dea482ab64caa7b9f6aba1c6bf48bb6a5448d1c0f1b17ab42ad8c763a5344d"
  end

  resource "cffi" do
    url "https://files.pythonhosted.org/packages/eb/56/b1ba7935a17738ae8453301356628e8147c79dbb825bcbc73dc7401f9846/cffi-2.0.0.tar.gz"
    sha256 "44d1b5909021139fe36001ae048dbdde8214afa20200eda0f64c068cac5d5529"
  end

  resource "charset-normalizer" do
    url "https://files.pythonhosted.org/packages/e7/a1/67fe25fac3c7642725500a3f6cfe5821ad557c3abb11c9d20d12c7008d3e/charset_normalizer-3.4.7.tar.gz"
    sha256 "ae89db9e5f98a11a4bf50407d4363e7b09b31e55bc117b4f7d80aab97ba009e5"
  end

  resource "cryptography" do
    url "https://files.pythonhosted.org/packages/9f/a9/db8f313fdcd85d767d4973515e1db101f9c71f95fced83233de224673757/cryptography-48.0.0.tar.gz"
    sha256 "5c3932f4436d1cccb036cb0eaef46e6e2db91035166f1ad6505c3c9d5a635920"
  end

  resource "idna" do
    url "https://files.pythonhosted.org/packages/b9/28/99c51f664567218d824af024c0251650fb27e4ca066df188dab0769c5b91/idna-3.17.tar.gz"
    sha256 "5eb0cb53bc467c12eadcf6de83163ad8527cec9416f44b9b61b19caedad2b87f"
  end

  resource "msal" do
    url "https://files.pythonhosted.org/packages/9a/99/d840198ecf6e8057bbc937f129ae940404485d736cda73253bbff9537f01/msal-1.37.0.tar.gz"
    sha256 "1b1672a33ee467c1d70b341bb16cafd51bb3c817147a95b93263794b03971bec"
  end

  resource "msal-extensions" do
    url "https://files.pythonhosted.org/packages/01/99/5d239b6156eddf761a636bded1118414d161bd6b7b37a9335549ed159396/msal_extensions-1.3.1.tar.gz"
    sha256 "c5b0fd10f65ef62b5f1d62f4251d51cbcaf003fcedae8c91b040a488614be1a4"
  end

  resource "pycparser" do
    url "https://files.pythonhosted.org/packages/1b/7d/92392ff7815c21062bea51aa7b87d45576f649f16458d78b7cf94b9ab2e6/pycparser-3.0.tar.gz"
    sha256 "600f49d217304a5902ac3c37e1281c9fe94e4d0489de643a9504c5cdfdfc6b29"
  end

  resource "pyjwt" do
    url "https://files.pythonhosted.org/packages/3b/81/58d0ac84e1ef3a3843791d6954d94c0b33d526c75eeb1efbce9d0a4c4077/pyjwt-2.13.0.tar.gz"
    sha256 "41571c89ca91598c79e8ef18a2d07367d4810fbbd6f637794879baf1b7703423"
  end

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
    sha256 "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
  end

  resource "requests" do
    url "https://files.pythonhosted.org/packages/ac/c3/e2a2b89f2d3e2179abd6d00ebd70bff6273f37fb3e0cc209f48b39d00cbf/requests-2.34.2.tar.gz"
    sha256 "f288924cae4e29463698d6d60bc6a4da69c89185ad1e0bcc4104f584e960b9ed"
  end

  resource "toon-format" do
    url "https://files.pythonhosted.org/packages/fa/15/d23d6d3e36aa4ec96dd5692bc7715fe17015b669e8f0d1c5c7fa906a3ceb/toon_format-0.9.0b1.tar.gz"
    sha256 "8f391dd6ad9677c78366bd8eb6762d064a2183f67b9b7da1f348fdb6ee8738e7"
  end

  resource "truststore" do
    url "https://files.pythonhosted.org/packages/53/a3/1585216310e344e8102c22482f6060c7a6ea0322b63e026372e6dcefcfd6/truststore-0.10.4.tar.gz"
    sha256 "9d91bd436463ad5e4ee4aba766628dd6cd7010cf3e2461756b3303710eebc301"
  end

  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/72/94/1a15dd82efb362ac84269196e94cf00f187f7ed21c242792a923cdb1c61f/typing_extensions-4.15.0.tar.gz"
    sha256 "0cea48d173cc12fa28ecabc3b837ea3cf6f38c6d1136f85cbaaf598984861466"
  end

  resource "urllib3" do
    url "https://files.pythonhosted.org/packages/53/0c/06f8b233b8fd13b9e5ee11424ef85419ba0d8ba0b3138bf360be2ff56953/urllib3-2.7.0.tar.gz"
    sha256 "231e0ec3b63ceb14667c67be60f2f2c40a518cb38b03af60abc813da26505f4c"
  end

  def install
    venv = virtualenv_create(libexec, "python3.14")
    ENV.prepend_path "PYTHONPATH", Formula["maturin"].opt_lib/"python3.14/site-packages"
    bootstrap_resources = %w[
      setuptools
      packaging
      wheel
      semantic-version
      setuptools-rust
      pycparser
      cffi
      flit-core
      cython
      pathspec
      pluggy
      trove-classifiers
      hatchling
      setuptools-scm
      hatch-vcs
    ]
    bootstrap_resources.each do |resource_name|
      venv.pip_install resource(resource_name), build_isolation: false
    end
    venv.pip_install resources.reject { |r| bootstrap_resources.include?(r.name) }, build_isolation: false
    venv.pip_install_and_link buildpath, build_isolation: false
    pkgshare.install "skills"
  end

  def post_install
    skill_dir = Pathname.new(ENV.fetch("SMITH_SKILL_DIR", "#{Dir.home}/.agents/skills/smith"))

    if skill_dir.symlink? || skill_dir.file?
      rm skill_dir
    elsif skill_dir.directory?
      rm_r skill_dir
    end

    skill_dir.dirname.mkpath
    cp_r pkgshare/"skills/smith", skill_dir
  end

  def caveats
    skill_dir = ENV.fetch("SMITH_SKILL_DIR", "#{Dir.home}/.agents/skills/smith")

    <<~EOS
      Smith mirrored its skill to:
        #{skill_dir}

      To mirror the skill to another location, run:
        SMITH_SKILL_DIR=/path/to/skills/smith brew postinstall faustodavid/tap/smith
    EOS
  end

  test do
    assert_match "usage:", shell_output("#{bin}/smith --help")
    assert_path_exists pkgshare/"skills/smith/SKILL.md"
  end
end
